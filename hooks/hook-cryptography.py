# Explicit PyInstaller collection hook for the encrypted cloud vault.
# CFFI's native backend is a top-level extension module and may be reached
# through a dependency at runtime, so collect it explicitly as a hidden import.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("cryptography")
cffi_datas, cffi_binaries, cffi_hiddenimports = collect_all("cffi")

datas += cffi_datas
binaries += cffi_binaries
hiddenimports += cffi_hiddenimports
hiddenimports += [
    "cffi",
    "_cffi_backend",
    "cryptography.exceptions",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.scrypt",
    "cryptography.hazmat.bindings._rust",
]
