use durparse::{parse_duration, ParseError};
use std::time::Duration;

#[test]
fn seconds() {
    assert_eq!(parse_duration("30s"), Ok(Duration::from_secs(30)));
}

#[test]
fn minutes() {
    assert_eq!(parse_duration("5m"), Ok(Duration::from_secs(300)));
}

#[test]
fn hours() {
    assert_eq!(parse_duration("2h"), Ok(Duration::from_secs(7200)));
}

#[test]
fn trims_whitespace() {
    assert_eq!(parse_duration("  10s \n"), Ok(Duration::from_secs(10)));
}

#[test]
fn empty_input() {
    assert_eq!(parse_duration(""), Err(ParseError::Empty));
    assert_eq!(parse_duration("   "), Err(ParseError::Empty));
}

#[test]
fn unit_without_number() {
    assert_eq!(parse_duration("s"), Err(ParseError::MissingNumber));
}

#[test]
fn unknown_unit() {
    assert_eq!(parse_duration("10x"), Err(ParseError::UnknownUnit("x".to_string())));
}

#[test]
fn number_overflow() {
    assert_eq!(parse_duration("99999999999999999999s"), Err(ParseError::Overflow));
}

#[test]
fn multiply_overflow() {
    assert_eq!(parse_duration("18446744073709551615h"), Err(ParseError::Overflow));
}
