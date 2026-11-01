#!/usr/bin/env -S cargo +nightly -Zscript
---cargo
[package]
edition = "2024"
[dependencies]
luck_core = "=0.4.0"
luck_minifier = "=0.4.0"
mlua = { version = "=0.12.2", features = ["lua52", "vendored"] }
tempfile = "=3.27.0"
---

//! Build: cargo +nightly -Zscript compress.rs
//! Test:  cargo +nightly -Zscript test --manifest-path compress.rs
use std::{collections::HashMap, error::Error, fs, io::Write, path::Path};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const EEPROM_SIZE: usize = 4096;

/// Pick the shortest Lua literal, preserving escapes, Unicode and line endings.
fn quote(text: &str) -> String {
    let mut choices = Vec::new();
    for delimiter in ['\'', '"'] {
        let mut literal = String::from(delimiter);
        for c in text.chars() {
            match c {
                '\\' => literal.push_str("\\\\"),
                '\n' => literal.push_str("\\n"),
                '\r' => literal.push_str("\\r"),
                c if c == delimiter => {
                    literal.push('\\');
                    literal.push(c);
                }
                c if c.is_ascii_control() => literal.push_str(&format!("\\{:03}", c as u32)),
                c => literal.push(c),
            }
        }
        literal.push(delimiter);
        choices.push(literal);
    }
    // Lua drops a leading newline and normalizes CR in long strings.
    if !text.starts_with('\n') && !text.contains('\r') {
        for level in 0.. {
            let equals = "=".repeat(level);
            let close = format!("]{equals}]");
            if format!("{text}{close}").find(&close) == Some(text.len()) {
                choices.push(format!("[{equals}[{text}{close}"));
                break;
            }
        }
    }
    choices.into_iter().min_by_key(String::len).unwrap()
}

/// One-pass replacement avoids re-decoding markers introduced by dictionary values.
fn wrapper(payload: &str, dictionary: &[(char, String)]) -> String {
    let mut pattern = String::from("[");
    let mut entries = Vec::new();
    for (marker, value) in dictionary {
        if matches!(marker, '%' | '^' | '-' | ']') {
            pattern.push('%');
        }
        pattern.push(*marker);
        entries.push(format!("[{}]={}", quote(&marker.to_string()), quote(value)));
    }
    pattern.push(']');
    format!(
        "load(({}):gsub({},{{{}}}))()",
        quote(payload),
        quote(&pattern),
        entries.join(",")
    )
}

fn pack(code: &str) -> String {
    // Only use printable markers absent from the original source, including literals.
    let markers: Vec<_> = "!$&?@`|^~#%*+-/:;<=>_{}"
        .chars()
        .filter(|c| !code.contains(*c))
        .collect();
    let mut payload = code.to_owned();
    let mut dictionary = Vec::new();
    let mut best = code.to_owned(); // A small program can be shorter without a decoder.
    for marker in markers {
        let boundaries: Vec<_> = payload
            .char_indices()
            .map(|(i, _)| i)
            .chain([payload.len()])
            .collect();
        let mut counts: HashMap<&str, usize> = HashMap::new();
        for start in 0..boundaries.len() - 1 {
            for end in start + 2..=(start + 40).min(boundaries.len() - 1) {
                let candidate = &payload[boundaries[start]..boundaries[end]];
                if dictionary.iter().any(|(m, _)| candidate.contains(*m)) {
                    break;
                }
                *counts.entry(candidate).or_default() += 1;
            }
        }
        // An upper bound filters candidates; exact sizes include quoting and decoder overhead.
        let mut candidates: Vec<_> = counts
            .into_iter()
            .filter_map(|(text, count)| {
                let gain = (text.len() - 1) * count;
                (count > 1 && gain > text.len() + 8).then_some((gain, text))
            })
            .collect();
        candidates.sort_unstable_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(b.1)));
        let mut choice: Option<(String, String, String)> = None;
        for (_, text) in candidates.into_iter().take(128) {
            let next_payload = payload.replace(text, &marker.to_string());
            let mut next_dictionary = dictionary.clone();
            next_dictionary.push((marker, text.to_owned()));
            let image = wrapper(&next_payload, &next_dictionary);
            if image.len() < best.len()
                && choice
                    .as_ref()
                    .is_none_or(|(_, _, current)| image.len() < current.len())
            {
                choice = Some((text.to_owned(), next_payload, image));
            }
        }
        let Some((text, next_payload, image)) = choice else {
            break;
        };
        dictionary.push((marker, text));
        payload = next_payload;
        best = image;
    }
    best
}

fn minify(source: &str) -> Result<String> {
    // Preserve control flow, calls and numeric evaluation across OC's Lua versions.
    // Only rename locals; do not inline, fold expressions or change numeric spellings.
    let config = luck_core::TransformConfig {
        remove_dead_code: false,
        simplify_statements: false,
        fold_constants: false,
        inline_locals: false,
        merge_locals: false,
        simplify_indexes: false,
        shorten_strings: false,
        shorten_numbers: false,
        simplify_parens: false,
        rename_locals: true,
        lift_locals: false,
        rename_globals: false,
    };
    let lua = mlua::Lua::new();
    lua.load(source).into_function()?;
    let code = luck_minifier::minify(source, luck_core::LuaTarget::Lua52, &config, "input.lua")
        .map_err(|errors| format!("Lua minification failed: {errors:?}"))?;
    lua.load(&code).into_function()?;
    Ok(code)
}

fn verify_round_trip(code: &str, image: &str) -> Result<()> {
    if code == image {
        return Ok(());
    }
    let lua = mlua::Lua::new();
    let decoded: mlua::LuaString = lua.load(format!(
        "local decoded;local load=function(code) decoded=code;return function() end end;{image};return decoded"
    )).eval()?;
    if decoded.as_bytes().as_ref() != code.as_bytes() {
        return Err("Dictionary round-trip mismatch".into());
    }
    Ok(())
}

fn build(source: &Path, destination: &Path, check: bool) -> Result<(usize, usize, usize)> {
    let original = fs::read_to_string(source)?;
    let minified = minify(&original)?;
    let image = pack(&minified);
    mlua::Lua::new().load(&image).into_function()?;
    verify_round_trip(&minified, &image)?;
    if image.len() > EEPROM_SIZE {
        return Err(format!(
            "EEPROM image is {} bytes; limit is {EEPROM_SIZE}",
            image.len()
        )
        .into());
    }
    if check {
        if fs::read(destination)? != image.as_bytes() {
            return Err("EEPROM image is out of date".into());
        }
    } else {
        // Same-directory temporary file: errors leave the previous image intact.
        let parent = destination
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
        temporary.write_all(image.as_bytes())?;
        temporary.as_file().sync_all()?;
        temporary.persist(destination)?;
    }
    Ok((original.len(), minified.len(), image.len()))
}

fn run() -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_PATH"))
        .parent()
        .ok_or("Missing script directory")?;
    let mut source = root.join("nuclear_SourceCode.lua");
    let mut destination = root.join("nuclear.lua");
    let mut check = false;
    let mut args = std::env::args_os().skip(1);
    while let Some(arg) = args.next() {
        match arg.to_str() {
            Some("--source") => source = args.next().ok_or("--source requires a path")?.into(),
            Some("--output") => destination = args.next().ok_or("--output requires a path")?.into(),
            Some("--check") => check = true,
            Some("--help" | "-h") => {
                println!(
                    "cargo +nightly -Zscript compress.rs [--source PATH] [--output PATH] [--check]"
                );
                return Ok(());
            }
            _ => return Err(format!("Unknown argument: {}", arg.to_string_lossy()).into()),
        }
    }
    let (original, minified, packed) = build(&source, &destination, check)?;
    println!(
        "{} {}: {original} -> {minified} -> {packed}/{EEPROM_SIZE} bytes",
        if check { "Verified" } else { "Built" },
        destination.display()
    );
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Build failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lua_literals_preserve_bytes() {
        let lua = mlua::Lua::new();
        for text in [
            "",
            "\nleading",
            "a\rb\r\nc",
            "'\"\\",
            "[[end]]]=]",
            "ending]",
            "中文\0\u{1}9",
        ] {
            let actual: mlua::LuaString =
                lua.load(format!("return {}", quote(text))).eval().unwrap();
            assert_eq!(actual.as_bytes().as_ref(), text.as_bytes());
        }
    }

    #[test]
    fn dictionary_round_trip_with_original_markers() {
        let code = "local text='!$&?@ local end function return then 中文';".repeat(25);
        let packed = pack(&code);
        assert!(packed.len() < code.len());
        let decoded: String = mlua::Lua::new().load(format!(
            "local decoded;local load=function(code) decoded=code;return function() end end;{packed};return decoded"
        )).eval().unwrap();
        assert_eq!(decoded, code);
        assert_eq!(packed, pack(&code));
    }

    #[test]
    fn small_program_needs_no_wrapper() {
        assert_eq!(pack("return 1"), "return 1");
    }

    #[test]
    fn build_is_atomic_and_check_does_not_write() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.lua");
        let output = directory.path().join("nuclear.lua");
        fs::write(&output, "previous firmware").unwrap();
        let mut random = 12345_u32;
        let large: String = (0..10000)
            .map(|_| {
                random = random.wrapping_mul(1664525).wrapping_add(1013904223);
                (b'a' + ((random >> 16) % 26) as u8) as char
            })
            .collect();
        for text in ["local =", &format!("return {large:?}")] {
            fs::write(&source, text).unwrap();
            assert!(build(&source, &output, false).is_err());
            assert_eq!(fs::read_to_string(&output).unwrap(), "previous firmware");
            assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 2);
        }
        fs::write(&source, "return 42").unwrap();
        assert!(build(&source, &output, true).is_err());
        assert_eq!(fs::read_to_string(&output).unwrap(), "previous firmware");
        build(&source, &output, false).unwrap();
        build(&source, &output, true).unwrap();
    }

    #[test]
    fn minifier_preserves_scope_calls_and_multiple_returns() {
        let source = r#"
            local external = 7
            local function factory(value)
                local external = value
                return function() return external, 1.0 end
            end
            local closure = factory(11)
            local function identity(...) return ... end
            local a,b = identity(closure())
            goto DONE
            ::DONE::
            return tostring(external)..":"..a..":"..tostring(b)
        "#;
        let minified = minify(source).unwrap();
        let lua = mlua::Lua::new();
        let original: String = lua.load(source).eval().unwrap();
        let actual: String = lua.load(&minified).eval().unwrap();
        assert_eq!(actual, original);
        verify_round_trip(&minified, &pack(&minified)).unwrap();
    }

    #[test]
    fn firmware_dictionary_round_trip() {
        let source = include_str!("nuclear_SourceCode.lua");
        let minified = minify(source).unwrap();
        let image = pack(&minified);
        assert!(image.len() <= EEPROM_SIZE);
        verify_round_trip(&minified, &image).unwrap();
    }
}
