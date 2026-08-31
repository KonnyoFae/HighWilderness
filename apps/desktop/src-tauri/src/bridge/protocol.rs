use std::collections::BTreeSet;
use std::fmt;

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

pub const BRIDGE_INTERFACE: &str = "gaotian.web-bridge/v1alpha1";
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
const MAX_JSON_DEPTH: usize = 64;
const MAX_SAFE_INTEGER: u64 = (1_u64 << 53) - 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolFailure {
    pub code: &'static str,
    pub path: String,
    pub message: String,
}

impl ProtocolFailure {
    fn new(code: &'static str, path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code,
            path: path.into(),
            message: message.into(),
        }
    }
}

impl fmt::Display for ProtocolFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "[{}] {}: {}", self.code, self.path, self.message)
    }
}

impl std::error::Error for ProtocolFailure {}

struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = StrictValue;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON value without duplicate object keys")
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(value)))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(Number::from(value))))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(Number::from(value))))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                Number::from_f64(value)
                    .map(|number| StrictValue(Value::Number(number)))
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value.to_owned())))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value)))
            }

            fn visit_none<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
            where
                D: Deserializer<'de>,
            {
                StrictValue::deserialize(deserializer)
            }

            fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut values = Vec::new();
                while let Some(StrictValue(value)) = sequence.next_element::<StrictValue>()? {
                    values.push(value);
                }
                Ok(StrictValue(Value::Array(values)))
            }

            fn visit_map<A>(self, mut access: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut values = Map::new();
                while let Some(key) = access.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom(format!("duplicate JSON key: {key}")));
                    }
                    let StrictValue(value) = access.next_value::<StrictValue>()?;
                    values.insert(key, value);
                }
                Ok(StrictValue(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(StrictVisitor)
    }
}

fn failure(
    code: &'static str,
    path: impl Into<String>,
    message: impl Into<String>,
) -> ProtocolFailure {
    ProtocolFailure::new(code, path, message)
}

fn validate_json(value: &Value, path: &str, depth: usize) -> Result<(), ProtocolFailure> {
    if depth > MAX_JSON_DEPTH {
        return Err(failure(
            "bridge.invalid_json",
            path,
            "JSON nesting exceeds 64 levels",
        ));
    }
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => Ok(()),
        Value::Number(number) => {
            if let Some(value) = number.as_u64() {
                if value > MAX_SAFE_INTEGER {
                    return Err(failure(
                        "bridge.invalid_json",
                        path,
                        "integer exceeds JavaScript safe range",
                    ));
                }
            } else if let Some(value) = number.as_i64() {
                if value.unsigned_abs() > MAX_SAFE_INTEGER {
                    return Err(failure(
                        "bridge.invalid_json",
                        path,
                        "integer exceeds JavaScript safe range",
                    ));
                }
            } else if number.as_f64().is_none_or(|value| !value.is_finite()) {
                return Err(failure(
                    "bridge.invalid_json",
                    path,
                    "number must be finite",
                ));
            }
            Ok(())
        }
        Value::Array(values) => values.iter().enumerate().try_for_each(|(index, item)| {
            validate_json(item, &format!("{path}[{index}]"), depth + 1)
        }),
        Value::Object(values) => values
            .iter()
            .try_for_each(|(key, item)| validate_json(item, &format!("{path}.{key}"), depth + 1)),
    }
}

fn object<'a>(value: &'a Value, path: &str) -> Result<&'a Map<String, Value>, ProtocolFailure> {
    value
        .as_object()
        .ok_or_else(|| failure("bridge.invalid_message", path, "value must be an object"))
}

fn exact_fields(
    object: &Map<String, Value>,
    expected: &[&str],
    path: &str,
) -> Result<(), ProtocolFailure> {
    let actual: BTreeSet<_> = object.keys().map(String::as_str).collect();
    let expected: BTreeSet<_> = expected.iter().copied().collect();
    if actual != expected {
        return Err(failure(
            "bridge.invalid_message",
            path,
            "message field set does not match",
        ));
    }
    Ok(())
}

fn string<'a>(object: &'a Map<String, Value>, key: &str) -> Result<&'a str, ProtocolFailure> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            failure(
                "bridge.invalid_message",
                format!("$.{key}"),
                "must be a non-empty string",
            )
        })
}

fn valid_id(value: &str) -> bool {
    (1..=128).contains(&value.len())
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_lowercase_or_digit()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase_or_digit() || matches!(byte, b'.' | b'_' | b'-'))
}

trait AsciiIdentifier {
    fn is_ascii_lowercase_or_digit(&self) -> bool;
}

impl AsciiIdentifier for u8 {
    fn is_ascii_lowercase_or_digit(&self) -> bool {
        self.is_ascii_lowercase() || self.is_ascii_digit()
    }
}

fn valid_request_id(value: &str) -> bool {
    let Some(number) = value.strip_prefix("req.") else {
        return false;
    };
    (1..=15).contains(&number.len())
        && !number.starts_with('0')
        && number.bytes().all(|byte| byte.is_ascii_digit())
}

fn valid_name(value: &str) -> bool {
    let mut parts = value.split('.');
    let namespace = parts.next();
    if !matches!(
        namespace,
        Some("system" | "editor" | "resource" | "save" | "tactical" | "strategy")
    ) {
        return false;
    }
    let remainder: Vec<_> = parts.collect();
    !remainder.is_empty()
        && remainder.iter().all(|part| {
            !part.is_empty()
                && part.as_bytes()[0].is_ascii_lowercase()
                && part
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase_or_digit() || byte == b'_')
        })
}

fn revision(value: &Value, path: &str) -> Result<Option<u64>, ProtocolFailure> {
    if value.is_null() {
        return Ok(None);
    }
    value
        .as_u64()
        .filter(|number| *number <= MAX_SAFE_INTEGER)
        .map(Some)
        .ok_or_else(|| {
            failure(
                "bridge.invalid_message",
                path,
                "revision must be a safe non-negative integer",
            )
        })
}

fn validate_error(value: &Value) -> Result<(), ProtocolFailure> {
    let error = object(value, "$.error")?;
    exact_fields(
        error,
        &["code", "path", "message", "source", "retryable", "details"],
        "$.error",
    )?;
    for field in ["code", "path", "message"] {
        string(error, field)?;
    }
    if !matches!(string(error, "source")?, "host" | "bridge" | "domain") {
        return Err(failure(
            "bridge.invalid_message",
            "$.error.source",
            "unknown error source",
        ));
    }
    if error.get("retryable").and_then(Value::as_bool).is_none() {
        return Err(failure(
            "bridge.invalid_message",
            "$.error.retryable",
            "must be boolean",
        ));
    }
    object(
        error.get("details").unwrap_or(&Value::Null),
        "$.error.details",
    )?;
    Ok(())
}

pub fn validate_message(value: &Value) -> Result<(), ProtocolFailure> {
    validate_json(value, "$", 0)?;
    let message = object(value, "$")?;
    if string(message, "interface")? != BRIDGE_INTERFACE {
        return Err(failure(
            "bridge.unsupported_interface",
            "$.interface",
            "bridge interface must match",
        ));
    }
    let kind = string(message, "kind")?;
    let fields: &[&str] = match kind {
        "request" => &[
            "interface",
            "kind",
            "backend_instance_id",
            "request_id",
            "session_id",
            "expected_revision",
            "method",
            "params",
        ],
        "response" => &[
            "interface",
            "kind",
            "backend_instance_id",
            "request_id",
            "session_id",
            "revision",
            "ok",
            "result",
            "error",
        ],
        "event" => &[
            "interface",
            "kind",
            "backend_instance_id",
            "session_id",
            "revision",
            "sequence",
            "event",
            "payload",
        ],
        _ => {
            return Err(failure(
                "bridge.invalid_message",
                "$.kind",
                "unknown message kind",
            ));
        }
    };
    exact_fields(message, fields, "$")?;
    if !valid_id(string(message, "backend_instance_id")?) {
        return Err(failure(
            "bridge.invalid_message",
            "$.backend_instance_id",
            "invalid identifier",
        ));
    }
    let session = match message.get("session_id") {
        Some(Value::Null) => None,
        Some(Value::String(value)) if valid_id(value) => Some(value.as_str()),
        _ => {
            return Err(failure(
                "bridge.invalid_message",
                "$.session_id",
                "invalid editor session identifier",
            ));
        }
    };
    let revision_key = if kind == "request" {
        "expected_revision"
    } else {
        "revision"
    };
    let current_revision = revision(
        message.get(revision_key).unwrap(),
        &format!("$.{revision_key}"),
    )?;
    if session.is_none() && current_revision.is_some() {
        return Err(failure(
            "bridge.invalid_message",
            format!("$.{revision_key}"),
            "global message cannot carry revision",
        ));
    }
    if kind != "event" && !valid_request_id(string(message, "request_id")?) {
        return Err(failure(
            "bridge.invalid_message",
            "$.request_id",
            "invalid monotone request identifier",
        ));
    }
    match kind {
        "request" => {
            let method = string(message, "method")?;
            if !valid_name(method) {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.method",
                    "invalid method name",
                ));
            }
            object(message.get("params").unwrap(), "$.params")?;
            if session.is_some() && current_revision.is_none() {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.expected_revision",
                    "editor session request needs revision",
                ));
            }
            if session.is_some() && !(method.starts_with("editor.") || method.starts_with("save."))
            {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.session_id",
                    "namespace does not use editor session",
                ));
            }
        }
        "response" => {
            let ok = message
                .get("ok")
                .and_then(Value::as_bool)
                .ok_or_else(|| failure("bridge.invalid_message", "$.ok", "ok must be boolean"))?;
            if ok {
                object(message.get("result").unwrap(), "$.result")?;
                if !message.get("error").unwrap().is_null() {
                    return Err(failure(
                        "bridge.invalid_message",
                        "$.error",
                        "success cannot include error",
                    ));
                }
                if session.is_some() && current_revision.is_none() {
                    return Err(failure(
                        "bridge.invalid_message",
                        "$.revision",
                        "session success needs revision",
                    ));
                }
            } else {
                if !message.get("result").unwrap().is_null() {
                    return Err(failure(
                        "bridge.invalid_message",
                        "$.result",
                        "failure cannot include result",
                    ));
                }
                validate_error(message.get("error").unwrap())?;
            }
        }
        "event" => {
            let event = string(message, "event")?;
            if !valid_name(event) {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.event",
                    "invalid event name",
                ));
            }
            let sequence = message.get("sequence").and_then(Value::as_u64).unwrap_or(0);
            if sequence == 0 || sequence > MAX_SAFE_INTEGER {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.sequence",
                    "invalid event sequence",
                ));
            }
            object(message.get("payload").unwrap(), "$.payload")?;
            if session.is_some() && current_revision.is_none() {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.revision",
                    "session event needs revision",
                ));
            }
            if session.is_some() && !(event.starts_with("editor.") || event.starts_with("save.")) {
                return Err(failure(
                    "bridge.invalid_message",
                    "$.session_id",
                    "event namespace does not use editor session",
                ));
            }
        }
        _ => unreachable!(),
    }
    Ok(())
}

pub fn decode_line(line: &[u8]) -> Result<Value, ProtocolFailure> {
    if line.len() > MAX_FRAME_BYTES {
        return Err(failure(
            "bridge.frame_too_large",
            "$",
            "frame exceeds byte limit",
        ));
    }
    if !line.ends_with(b"\n") {
        return Err(failure(
            "bridge.truncated_frame",
            "$",
            "frame must end in LF",
        ));
    }
    let mut payload = &line[..line.len() - 1];
    if payload.ends_with(b"\r") {
        payload = &payload[..payload.len() - 1];
    }
    if payload.contains(&b'\r') || payload.contains(&b'\n') {
        return Err(failure(
            "bridge.invalid_json",
            "$",
            "one frame must contain one JSON line",
        ));
    }
    let text = std::str::from_utf8(payload)
        .map_err(|_| failure("bridge.invalid_json", "$", "frame must be strict UTF-8"))?;
    if text.starts_with('\u{feff}') {
        return Err(failure(
            "bridge.invalid_json",
            "$",
            "UTF-8 BOM is forbidden",
        ));
    }
    let mut deserializer = serde_json::Deserializer::from_str(text);
    let StrictValue(value) = StrictValue::deserialize(&mut deserializer)
        .map_err(|error| failure("bridge.invalid_json", "$", error.to_string()))?;
    deserializer
        .end()
        .map_err(|error| failure("bridge.invalid_json", "$", error.to_string()))?;
    validate_message(&value)?;
    Ok(value)
}

pub fn encode_message(value: &Value) -> Result<Vec<u8>, ProtocolFailure> {
    validate_message(value)?;
    let mut wire = serde_json::to_vec(value)
        .map_err(|error| failure("bridge.invalid_json", "$", error.to_string()))?;
    wire.push(b'\n');
    if wire.len() > MAX_FRAME_BYTES {
        return Err(failure(
            "bridge.frame_too_large",
            "$",
            "frame exceeds byte limit",
        ));
    }
    Ok(wire)
}

#[derive(Default)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    failed: bool,
}

impl FrameDecoder {
    pub fn feed(&mut self, chunk: &[u8]) -> Result<Vec<Value>, ProtocolFailure> {
        if self.failed {
            return Err(failure("bridge.decoder_closed", "$", "decoder is closed"));
        }
        let mut messages = Vec::new();
        for byte in chunk {
            self.buffer.push(*byte);
            if self.buffer.len() > MAX_FRAME_BYTES
                || (self.buffer.len() == MAX_FRAME_BYTES && *byte != b'\n')
            {
                self.failed = true;
                self.buffer.clear();
                return Err(failure(
                    "bridge.frame_too_large",
                    "$",
                    "frame exceeds byte limit",
                ));
            }
            if *byte == b'\n' {
                match decode_line(&self.buffer) {
                    Ok(message) => messages.push(message),
                    Err(error) => {
                        self.failed = true;
                        self.buffer.clear();
                        return Err(error);
                    }
                }
                self.buffer.clear();
            }
        }
        Ok(messages)
    }

    pub fn finish(&mut self) -> Result<(), ProtocolFailure> {
        if self.failed {
            return Err(failure("bridge.decoder_closed", "$", "decoder is closed"));
        }
        self.failed = true;
        if self.buffer.is_empty() {
            Ok(())
        } else {
            self.buffer.clear();
            Err(failure("bridge.truncated_frame", "$", "EOF before LF"))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn contract(name: &str) -> Value {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let text = fs::read_to_string(root.join("contracts/web_bridge").join(name)).unwrap();
        serde_json::from_str(&text).unwrap()
    }

    #[test]
    fn examples_round_trip_and_split_at_every_byte() {
        let examples = contract("examples.v1alpha1.json");
        for message in examples["messages"].as_object().unwrap().values() {
            validate_message(message).unwrap();
            let wire = encode_message(message).unwrap();
            assert_eq!(decode_line(&wire).unwrap(), *message);
            for split in 0..=wire.len() {
                let mut decoder = FrameDecoder::default();
                let mut decoded = decoder.feed(&wire[..split]).unwrap();
                decoded.extend(decoder.feed(&wire[split..]).unwrap());
                assert_eq!(decoded, vec![message.clone()]);
                decoder.finish().unwrap();
            }
        }
    }

    #[test]
    fn portable_negative_corpus_matches_expected_codes() {
        let negative = contract("negative-cases.v1.json");
        for case in negative["cases"].as_array().unwrap() {
            let mode = case["mode"].as_str().unwrap();
            if mode == "hello" {
                continue; // Handshake semantics are exercised by the real Python process tests.
            }
            let result = match mode {
                "message" => validate_message(&case["input"]),
                "wire_utf8" => decode_line(case["input"].as_str().unwrap().as_bytes()).map(|_| ()),
                "wire_hex" => {
                    let hex = case["input"].as_str().unwrap();
                    let bytes: Vec<u8> = (0..hex.len())
                        .step_by(2)
                        .map(|index| u8::from_str_radix(&hex[index..index + 2], 16).unwrap())
                        .collect();
                    decode_line(&bytes).map(|_| ())
                }
                _ => panic!("unknown negative mode {mode}"),
            };
            let error = result.expect_err(case["id"].as_str().unwrap());
            assert_eq!(
                error.code,
                case["error_code"].as_str().unwrap(),
                "{}",
                case["id"]
            );
        }
    }

    #[test]
    fn duplicate_keys_and_eof_poison_decoder() {
        assert_eq!(
            decode_line(b"{\"interface\":\"x\",\"interface\":\"y\"}\n")
                .unwrap_err()
                .code,
            "bridge.invalid_json"
        );
        let examples = contract("examples.v1alpha1.json");
        let wire = encode_message(&examples["messages"]["ping_request"]).unwrap();
        let mut decoder = FrameDecoder::default();
        decoder.feed(&wire[..wire.len() - 1]).unwrap();
        assert_eq!(decoder.finish().unwrap_err().code, "bridge.truncated_frame");
        assert_eq!(
            decoder.feed(b"\n").unwrap_err().code,
            "bridge.decoder_closed"
        );
    }
}
