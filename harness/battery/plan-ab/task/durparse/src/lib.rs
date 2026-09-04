//! durparse — parse human-friendly duration strings such as `"30s"`, `"5m"`, `"2h"`.
//!
//! ```
//! use std::time::Duration;
//! assert_eq!(durparse::parse_duration("5m"), Ok(Duration::from_secs(300)));
//! ```

use std::time::Duration;

/// Why a duration string could not be parsed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// The input was empty (after trimming).
    Empty,
    /// A unit appeared with no number in front of it (`"s"`).
    MissingNumber,
    /// The unit is not one this crate knows.
    UnknownUnit(String),
    /// The number does not fit, or the total does not fit, in a `Duration`.
    Overflow,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::Empty => write!(f, "empty duration"),
            ParseError::MissingNumber => write!(f, "unit without a number"),
            ParseError::UnknownUnit(u) => write!(f, "unknown unit {u:?}"),
            ParseError::Overflow => write!(f, "duration out of range"),
        }
    }
}

impl std::error::Error for ParseError {}

/// Parse a single `<number><unit>` term. Known units: `s`, `m`, `h`.
///
/// Leading and trailing whitespace is ignored.
pub fn parse_duration(s: &str) -> Result<Duration, ParseError> {
    let s = s.trim();
    if s.is_empty() {
        return Err(ParseError::Empty);
    }
    let idx = s
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(s.len());
    let (num, unit) = s.split_at(idx);
    if num.is_empty() {
        return Err(ParseError::MissingNumber);
    }
    let n: u64 = num.parse().map_err(|_| ParseError::Overflow)?;
    let secs = match unit {
        "s" => n,
        "m" => n.checked_mul(60).ok_or(ParseError::Overflow)?,
        "h" => n.checked_mul(3600).ok_or(ParseError::Overflow)?,
        other => return Err(ParseError::UnknownUnit(other.to_string())),
    };
    Ok(Duration::from_secs(secs))
}
