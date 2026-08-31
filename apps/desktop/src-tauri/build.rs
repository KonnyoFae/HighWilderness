use std::path::PathBuf;

// W1 has no product artwork yet. Generate the smallest useful Windows resource
// icon in Cargo's output directory so the build stays reproducible without
// committing a placeholder binary asset.
const PLACEHOLDER_ICO: &[u8] = &[
    0x00, 0x00, 0x01, 0x00, 0x01, 0x00, // ICONDIR
    0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x20, 0x00, 0x30, 0x00, 0x00, 0x00, 0x16, 0x00, 0x00,
    0x00, // ICONDIRENTRY
    0x28, 0x00, 0x00, 0x00, // BITMAPINFOHEADER size
    0x01, 0x00, 0x00, 0x00, // width
    0x02, 0x00, 0x00, 0x00, // height (XOR + AND)
    0x01, 0x00, 0x20, 0x00, // planes, bits per pixel
    0x00, 0x00, 0x00, 0x00, // compression
    0x04, 0x00, 0x00, 0x00, // image size
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // pixels per metre
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // palette metadata
    0x58, 0xb8, 0x92, 0xff, // one opaque BGRA pixel
    0x00, 0x00, 0x00, 0x00, // AND mask, padded to one DWORD
];

fn main() {
    let output_directory = PathBuf::from(
        std::env::var_os("CARGO_MANIFEST_DIR")
            .expect("Cargo must define CARGO_MANIFEST_DIR for the build script"),
    )
    .join("target");
    std::fs::create_dir_all(&output_directory)
        .expect("failed to create the local Cargo target directory");
    let icon_path = output_directory.join("w1-placeholder.ico");
    std::fs::write(&icon_path, PLACEHOLDER_ICO).expect("failed to write the generated W1 icon");

    let windows = tauri_build::WindowsAttributes::new().window_icon_path(icon_path);
    let attributes = tauri_build::Attributes::new().windows_attributes(windows);
    tauri_build::try_build(attributes).expect("failed to run the Tauri build script");
}
