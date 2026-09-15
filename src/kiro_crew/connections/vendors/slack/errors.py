"""A NARROW, TEMPORARY seam: Slack native error string -> local classification.

Why this exists
---------------
The campaign's single source of truth for connector error classification is the
RUN-01 taxonomy owned by the W01 control-plane slice
(``kiro_crew.connections.control_plane.errors``). That module is not landed on
``main`` yet, so this Slack slice cannot import it — and it must NOT fork the
enum, because two copies of one closed set is exactly the drift the campaign is
built to prevent.

So this module is the one seam the slice keeps: a single boundary that takes a
Slack-native error string (the value ``slack_sdk`` surfaces in
``SlackApiError.response["error"]``, or the HTTP status of a non-200) and returns
a small local classification. It is deliberately:

- **local and temporary.** :class:`SlackErrorKind` is this module's own type, not
  RUN-01. When W01 lands, an adapter maps :class:`SlackErrorKind` onto RUN-01 in
  one place; nothing else in the slice changes. The kinds are named after the
  *decision a caller makes* (retry? re-auth? give up?), not after RUN-01's
  vocabulary, precisely so a reader is never fooled into thinking this is the
  shared set.
- **evidence-built, not invented.** Every string in the maps below is a literal
  Slack error code taken from the official method reference pages
  (api.slack.com/methods/*). New strings are added by reading the page, never by
  guessing.
- **pure.** No IO, no credentials, no ``slack_sdk`` import. It classifies strings.

The classification a caller reads
---------------------------------
:func:`classify_slack_error` is the entry point. It accepts either a Slack error
string, or an HTTP status int (for the non-200 half — a real 429/5xx never
carries an ``ok:false`` body), and returns a :class:`SlackErrorClassification`
carrying the kind, whether a retry is even worth attempting, and whether the
failure is the caller's own fault (a permanent 4xx-class mistake).

Retry ownership
---------------
``kiro_crew.slack.retry.is_retryable_slack_error`` owns the exception-path
decision used by production callers: HTTP 429 and 5xx are retryable, while
other 4xx responses are final. This module is the string/kind projection of
that rule for callers that hold only a Slack error code or status. Therefore
:attr:`SlackErrorKind.THROTTLE` and :attr:`SlackErrorKind.TEMPORARY` are
retryable, and every other kind is final.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SlackErrorKind(str, Enum):
    """This slice's OWN local error classes — NOT the RUN-01 taxonomy.

    Named for the caller's decision, not RUN-01's vocabulary. An adapter maps
    these onto RUN-01 in one place once that shared module lands.
    """

    #: The credential/grant itself was rejected (re-auth needed).
    AUTH = "auth"
    #: The credential is valid but lacks the OAuth scope this method needs.
    SCOPE = "scope"
    #: The addressed resource (channel, message, file, user) does not exist or
    #: is invisible to the token.
    NOT_FOUND = "not_found"
    #: The resource exists but the caller is not permitted to act on it.
    FORBIDDEN = "forbidden"
    #: A rate limit asked the caller to slow down (Retry-After).
    THROTTLE = "throttle"
    #: A hard quota / storage ceiling was reached (not a transient throttle).
    QUOTA = "quota"
    #: The operation conflicts with the resource's current state.
    CONFLICT = "conflict"
    #: The request itself was malformed / invalid arguments.
    INPUT = "input"
    #: A transient server-side failure that may succeed on retry (5xx, timeouts).
    TEMPORARY = "temporary"
    #: A Slack error string this seam does not yet map. Fail closed: an unknown
    #: is treated as the caller's fault (not retried) so an unmapped code never
    #: becomes a silent infinite-retry loop.
    UNKNOWN = "unknown"


# --- Evidence-built maps: literal Slack error codes -> local kind -----------
# Sources: api.slack.com/methods/{chat.postMessage, conversations.*,
# files.getUploadURLExternal, files.completeUploadExternal, files.list,
# reactions.add, users.list, ...}. Every key is copied from a reference page.

#: Auth: the token/grant is bad. Re-auth, never retry as-is.
_AUTH_CODES = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "token_expired",
        "token_revoked",
        "no_authed_user",
    }
)

#: Scope: valid credential, missing OAuth scope or disallowed token type.
_SCOPE_CODES = frozenset(
    {
        "missing_scope",
        "not_allowed_token_type",
        "team_access_not_granted",
    }
)

#: Not-found: the resource is absent or invisible to this token.
_NOT_FOUND_CODES = frozenset(
    {
        "channel_not_found",
        "message_not_found",
        "user_not_found",
        "file_not_found",
        "file_deleted",
        "users_not_found",
        "thread_not_found",
        "unknown_method",
    }
)

#: Forbidden: exists, but this subject may not act on it.
_FORBIDDEN_CODES = frozenset(
    {
        "not_in_channel",
        "is_archived",
        "no_permission",
        "access_denied",
        "ekm_access_denied",
        "restricted_action",
        "user_is_external_guest",
        "cannot_dm_bot",
        "file_uploads_disabled",
        "file_uploads_except_images_disabled",
    }
)

#: Throttle: slow down and retry per Retry-After.
_THROTTLE_CODES = frozenset({"ratelimited", "rate_limited"})

#: Quota: a hard ceiling was reached; retrying the same call will not help.
_QUOTA_CODES = frozenset(
    {
        "storage_limit_reached",
        "file_upload_size_restricted",
        "msg_too_long",
        "too_many_attachments",
    }
)

#: Conflict: clashes with current resource state.
_CONFLICT_CODES = frozenset(
    {
        "already_reacted",
        "already_in_channel",
        "already_pinned",
        "already_starred",
        "message_not_modified",
    }
)

#: Input: malformed request / invalid arguments.
_INPUT_CODES = frozenset(
    {
        "invalid_arguments",
        "invalid_arg_name",
        "invalid_array_arg",
        "invalid_charset",
        "invalid_form_data",
        "invalid_post_type",
        "missing_post_type",
        "missing_argument",
        "invalid_blocks",
        "invalid_blocks_format",
        "invalid_cursor",
        "unknown_type",
        "unknown_snippet_type",
        "unknown_subtype",
        "snippet_too_large",
        "alt_txt_too_large",
        "no_text",
        "file_type_not_allowed",
    }
)

#: Temporary: transient server-side failures worth a retry.
_TEMPORARY_CODES = frozenset(
    {
        "internal_error",
        "fatal_error",
        "service_unavailable",
        "request_timeout",
    }
)

# One flat lookup, asserted disjoint at import so a code never lands in two kinds.
_CODE_TO_KIND: dict[str, SlackErrorKind] = {}
for _codes, _kind in (
    (_AUTH_CODES, SlackErrorKind.AUTH),
    (_SCOPE_CODES, SlackErrorKind.SCOPE),
    (_NOT_FOUND_CODES, SlackErrorKind.NOT_FOUND),
    (_FORBIDDEN_CODES, SlackErrorKind.FORBIDDEN),
    (_THROTTLE_CODES, SlackErrorKind.THROTTLE),
    (_QUOTA_CODES, SlackErrorKind.QUOTA),
    (_CONFLICT_CODES, SlackErrorKind.CONFLICT),
    (_INPUT_CODES, SlackErrorKind.INPUT),
    (_TEMPORARY_CODES, SlackErrorKind.TEMPORARY),
):
    for _code in _codes:
        if _code in _CODE_TO_KIND:  # pragma: no cover - guarded by a unit test
            raise AssertionError(
                f"Slack error code {_code!r} mapped to two kinds — the maps must " f"be disjoint"
            )
        _CODE_TO_KIND[_code] = _kind

#: Which kinds are worth another attempt. Only a throttle or a transient
#: server-side failure; everything else is the caller's own fault and will fail
#: identically forever, so retrying only burns quota. This mirrors the existing
#: rule in ``slack/retry.py`` (a 4xx other than 429 is final) but expressed over
#: the kind rather than an exception, so a caller with only an error string can
#: still make the decision.
_RETRYABLE_KINDS = frozenset({SlackErrorKind.THROTTLE, SlackErrorKind.TEMPORARY})


@dataclass(frozen=True)
class SlackErrorClassification:
    """The local decision this seam hands an upper layer for one failure.

    ``kind`` — this module's own :class:`SlackErrorKind` (NOT RUN-01).
    ``code`` — the original Slack error string when there was one, else ``None``
    (a non-200 with no ``ok:false`` body).
    ``retryable`` — whether another attempt is worth making at all.
    ``caller_fault`` — a permanent, request-side mistake (a 4xx-class error other
    than throttling): the same call will fail identically forever.
    """

    kind: SlackErrorKind
    code: Optional[str]
    retryable: bool
    caller_fault: bool


def classify_error_code(code: str) -> SlackErrorKind:
    """Map ONE Slack native error string to a local :class:`SlackErrorKind`.

    An unmapped or empty string is :attr:`SlackErrorKind.UNKNOWN` — fail closed,
    never guess a kind from the shape of the string (that free-text sniffing is
    the anti-pattern this seam replaces).
    """
    return _CODE_TO_KIND.get(code, SlackErrorKind.UNKNOWN)


def classify_http_status(status: int) -> SlackErrorKind:
    """Classify a NON-200 HTTP status — the half that is NOT an ``ok:false`` body.

    ``slack_sdk`` raises even on a real 429/5xx, but those carry no useful
    ``error`` string, so this seam classifies them from the status alone: 429 is
    a throttle, any other 5xx is transient, and any other non-2xx client status
    is a caller-fault input error. A 2xx reaching here is not an error at all and
    is reported UNKNOWN (the caller should not have classified a success).
    """
    if status == 429:
        return SlackErrorKind.THROTTLE
    if 500 <= status <= 599:
        return SlackErrorKind.TEMPORARY
    if 400 <= status <= 499:
        return SlackErrorKind.INPUT
    return SlackErrorKind.UNKNOWN


def classify_slack_error(
    *, code: Optional[str] = None, http_status: Optional[int] = None
) -> SlackErrorClassification:
    """Classify one Slack failure into the local decision an upper layer reads.

    Pass ``code`` for an ``ok:false`` body (the common case), or ``http_status``
    for a non-200 with no body. When both are given the error string wins,
    because a mapped code is more specific than its status class; an unmapped
    code with a status falls through to the status.

    Fails closed: an unknown kind is ``retryable=False, caller_fault=True`` so an
    unmapped code can never become an infinite retry.
    """
    kind = SlackErrorKind.UNKNOWN
    if code:
        kind = classify_error_code(code)
    if kind is SlackErrorKind.UNKNOWN and http_status is not None:
        kind = classify_http_status(http_status)

    retryable = kind in _RETRYABLE_KINDS
    # Caller-fault = a permanent request-side error. A throttle is transient
    # (not the caller's fault to fix by changing the request), and a transient
    # server failure is ours. UNKNOWN fails closed to caller_fault so it is not
    # retried.
    caller_fault = kind not in (SlackErrorKind.THROTTLE, SlackErrorKind.TEMPORARY)
    return SlackErrorClassification(
        kind=kind, code=code, retryable=retryable, caller_fault=caller_fault
    )
