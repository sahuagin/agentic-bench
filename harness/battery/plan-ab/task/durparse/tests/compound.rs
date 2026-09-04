//! Compound durations, the `d` and `ms` units, and the missing-unit error.
use durparse::{parse_duration, ParseError};
use std::time::Duration;

#[test]
fn hours_and_minutes() {
    assert_eq!(parse_duration("1h30m"), Ok(Duration::from_secs(5400)));
}

#[test]
fn minutes_and_seconds() {
    assert_eq!(parse_duration("1m30s"), Ok(Duration::from_secs(90)));
}

#[test]
fn three_terms_separated_by_spaces() {
    assert_eq!(
        parse_duration("2h 15m 10s"),
        Ok(Duration::from_secs(2 * 3600 + 15 * 60 + 10))
    );
}

#[test]
fn days() {
    assert_eq!(parse_duration("2d"), Ok(Duration::from_secs(172_800)));
}

#[test]
fn days_and_hours() {
    assert_eq!(parse_duration("1d12h"), Ok(Duration::from_secs(129_600)));
}

#[test]
fn milliseconds() {
    assert_eq!(parse_duration("500ms"), Ok(Duration::from_millis(500)));
}

#[test]
fn seconds_and_milliseconds() {
    assert_eq!(parse_duration("1s500ms"), Ok(Duration::from_millis(1500)));
}

#[test]
fn minutes_then_milliseconds() {
    assert_eq!(parse_duration("1m 250ms"), Ok(Duration::from_millis(60_250)));
}

#[test]
fn all_zero_terms() {
    assert_eq!(parse_duration("0h0m0s"), Ok(Duration::ZERO));
}

#[test]
fn number_without_unit_is_an_error() {
    assert_eq!(parse_duration("42"), Err(ParseError::MissingUnit));
}

#[test]
fn trailing_number_without_unit_is_an_error() {
    assert_eq!(parse_duration("1h30"), Err(ParseError::MissingUnit));
}

#[test]
fn unit_without_number_inside_compound() {
    assert_eq!(parse_duration("1h m"), Err(ParseError::MissingNumber));
}

#[test]
fn unknown_unit_inside_compound() {
    assert_eq!(parse_duration("1h30x"), Err(ParseError::UnknownUnit("x".to_string())));
}

#[test]
fn sum_overflow_does_not_panic() {
    assert_eq!(
        parse_duration("18446744073709551615s 1s"),
        Err(ParseError::Overflow)
    );
}
