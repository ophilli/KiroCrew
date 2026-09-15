"""Fault tests for the Slack error-classification seam.

Keyed on Slack's OWN native error strings (invalid_cursor, missing_scope,
channel_not_found, ratelimited, ...), NOT on any typed-error enum — the RUN-01
taxonomy is not landed and this slice must not depend on it. Every test drives a
FAILURE path; there is no happy-path-only coverage here.
"""

from __future__ import annotations

import pytest

from kiro_crew.connections.vendors.slack.errors import (
    SlackErrorKind,
    classify_error_code,
    classify_http_status,
    classify_slack_error,
)


@pytest.mark.parametrize(
    "code,expected",
    [
        # auth
        ("invalid_auth", SlackErrorKind.AUTH),
        ("not_authed", SlackErrorKind.AUTH),
        ("token_revoked", SlackErrorKind.AUTH),
        ("token_expired", SlackErrorKind.AUTH),
        # scope
        ("missing_scope", SlackErrorKind.SCOPE),
        ("not_allowed_token_type", SlackErrorKind.SCOPE),
        # not_found
        ("channel_not_found", SlackErrorKind.NOT_FOUND),
        ("message_not_found", SlackErrorKind.NOT_FOUND),
        ("user_not_found", SlackErrorKind.NOT_FOUND),
        # forbidden
        ("not_in_channel", SlackErrorKind.FORBIDDEN),
        ("is_archived", SlackErrorKind.FORBIDDEN),
        ("no_permission", SlackErrorKind.FORBIDDEN),
        # throttle
        ("ratelimited", SlackErrorKind.THROTTLE),
        # quota
        ("storage_limit_reached", SlackErrorKind.QUOTA),
        ("msg_too_long", SlackErrorKind.QUOTA),
        # conflict
        ("already_reacted", SlackErrorKind.CONFLICT),
        # input
        ("invalid_cursor", SlackErrorKind.INPUT),
        ("invalid_blocks", SlackErrorKind.INPUT),
        ("file_type_not_allowed", SlackErrorKind.INPUT),
        # temporary
        ("internal_error", SlackErrorKind.TEMPORARY),
        ("service_unavailable", SlackErrorKind.TEMPORARY),
    ],
)
def test_native_error_string_maps_to_expected_kind(code, expected):
    assert classify_error_code(code) is expected


def test_unmapped_error_string_is_unknown_and_fails_closed():
    # A code this seam has never seen must NOT be guessed from its shape.
    c = classify_slack_error(code="some_brand_new_slack_error")
    assert c.kind is SlackErrorKind.UNKNOWN
    assert c.retryable is False  # fail closed: never infinite-retry an unknown
    assert c.caller_fault is True


def test_empty_error_string_is_unknown():
    assert classify_error_code("") is SlackErrorKind.UNKNOWN


@pytest.mark.parametrize(
    "code",
    ["ratelimited", "internal_error", "service_unavailable", "fatal_error"],
)
def test_transient_and_throttle_are_retryable(code):
    c = classify_slack_error(code=code)
    assert c.retryable is True
    assert c.caller_fault is False


@pytest.mark.parametrize(
    "code",
    ["missing_scope", "channel_not_found", "invalid_auth", "invalid_cursor"],
)
def test_client_fault_errors_are_not_retryable(code):
    # A 4xx-class client fault fails identically forever; retrying burns quota.
    c = classify_slack_error(code=code)
    assert c.retryable is False
    assert c.caller_fault is True


def test_http_429_without_body_is_throttle_and_retryable():
    # The non-200 half: a real 429 carries no ok:false body, only a status.
    c = classify_slack_error(http_status=429)
    assert c.kind is SlackErrorKind.THROTTLE
    assert c.retryable is True
    assert c.code is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_http_5xx_without_body_is_temporary_and_retryable(status):
    c = classify_slack_error(http_status=status)
    assert c.kind is SlackErrorKind.TEMPORARY
    assert c.retryable is True


@pytest.mark.parametrize("status", [400, 403, 404, 413])
def test_http_4xx_without_body_is_input_caller_fault(status):
    c = classify_slack_error(http_status=status)
    assert c.kind is SlackErrorKind.INPUT
    assert c.retryable is False
    assert c.caller_fault is True


def test_error_code_wins_over_http_status_when_both_present():
    # A mapped code is more specific than its status class.
    c = classify_slack_error(code="missing_scope", http_status=429)
    assert c.kind is SlackErrorKind.SCOPE  # not THROTTLE


def test_unmapped_code_falls_through_to_http_status():
    c = classify_slack_error(code="totally_unknown", http_status=503)
    assert c.kind is SlackErrorKind.TEMPORARY


def test_2xx_status_is_not_an_error():
    # Classifying a success is caller misuse; report UNKNOWN, do not invent a fault.
    assert classify_http_status(200) is SlackErrorKind.UNKNOWN


def test_error_maps_are_disjoint():
    # Importing errors.py already asserts disjointness at module load; this makes
    # the guarantee an explicit regression check.
    from kiro_crew.connections.vendors.slack import errors as errmod

    seen: dict[str, SlackErrorKind] = {}
    for code, kind in errmod._CODE_TO_KIND.items():
        assert code not in seen, f"{code} in two kinds"
        seen[code] = kind


@pytest.mark.parametrize("status", [429, 500, 403, 404])
def test_http_retryability_matches_exception_path_rule(status):
    from types import SimpleNamespace

    from slack_sdk.errors import SlackApiError

    from kiro_crew.slack.retry import is_retryable_slack_error

    exc = SlackApiError(message="error", response=SimpleNamespace(status_code=status))
    assert classify_slack_error(http_status=status).retryable is is_retryable_slack_error(exc)
