# Explicit PyInstaller collection hook for the encrypted cloud vault.
# Collect package modules, metadata, data and native extension binaries.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("cryptography")
hiddenimports += [
    "cryptography.exceptions",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.scrypt",
    "cryptography.hazmat.bindings._rust",
]
