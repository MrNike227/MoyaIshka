# Explicit PyInstaller collection hook for cryptography and its CFFI runtime.
# CFFI's native _cffi_backend extension must be bundled whenever a dependency uses it.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("cryptography")
cffi_datas, cffi_binaries, cffi_hiddenimports = collect_all("cffi")
datas += cffi_datas
binaries += cffi_binaries
hiddenimports += cffi_hiddenimports
hiddenimports += [
    "cryptography.exceptions",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.scrypt",
    "cryptography.hazmat.bindings._rust",
    "cffi",
    "_cffi_backend",
]
