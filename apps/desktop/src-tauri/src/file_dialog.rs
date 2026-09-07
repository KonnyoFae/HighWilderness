//! Windows native file selection. Paths never enter the web-facing result.
use std::path::{Path, PathBuf};
use windows_sys::Win32::UI::Controls::Dialogs::*;

pub fn choose(owner: isize, save: bool, initial_dir: &Path) -> Result<Option<PathBuf>, String> {
    let mut buffer = vec![0u16; 32768];
    if save {
        for (i, c) in "舰艇设计.json".encode_utf16().enumerate() {
            buffer[i] = c;
        }
    }
    let filter: Vec<u16> = "舰艇设计 JSON 文件（船壳 / 舾装）\0*.json\0\0"
        .encode_utf16()
        .collect();
    let title: Vec<u16> = if save {
        "保存舰艇设计\0"
    } else {
        "打开舰艇设计\0"
    }
    .encode_utf16()
    .collect();
    let extension: Vec<u16> = "json\0".encode_utf16().collect();
    let initial: Vec<u16> = initial_dir
        .to_string_lossy()
        .encode_utf16()
        .chain([0])
        .collect();
    let mut dialog = OPENFILENAMEW {
        lStructSize: std::mem::size_of::<OPENFILENAMEW>() as u32,
        hwndOwner: owner as _,
        lpstrFilter: filter.as_ptr(),
        lpstrFile: buffer.as_mut_ptr(),
        nMaxFile: buffer.len() as u32,
        lpstrTitle: title.as_ptr(),
        lpstrDefExt: extension.as_ptr(),
        lpstrInitialDir: initial.as_ptr(),
        Flags: OFN_EXPLORER
            | OFN_NOCHANGEDIR
            | OFN_PATHMUSTEXIST
            | OFN_DONTADDTORECENT
            | if save {
                OFN_OVERWRITEPROMPT
            } else {
                OFN_FILEMUSTEXIST
            },
        ..Default::default()
    };
    // All pointers reference live UTF-16 buffers for the duration of the modal call.
    let accepted = unsafe {
        if save {
            GetSaveFileNameW(&mut dialog)
        } else {
            GetOpenFileNameW(&mut dialog)
        }
    };
    if accepted == 0 {
        let code = unsafe { CommDlgExtendedError() };
        return if code == 0 {
            Ok(None)
        } else {
            Err(format!("文件对话框失败：{code}"))
        };
    }
    let end = buffer.iter().position(|c| *c == 0).unwrap_or(buffer.len());
    Ok(Some(PathBuf::from(String::from_utf16_lossy(
        &buffer[..end],
    ))))
}
