# Проверенная сборка «Моя ИИшка» 4.1.5.2

Текущая опубликованная версия — 4.1.5.1. Цель этой ветки — выпустить 4.1.5.2 с одной канонической сборкой и обязательной проверкой криптографии.

## Что исправлено в ветке 4.1.5.2

- `.github/workflows/release.yml` теперь использует Node.js 24-совместимые `actions/checkout@v7` и `actions/setup-python@v7.1.0`. Node.js 20 actions удалены.
- Релиз публикуется встроенным GitHub CLI; дополнительное action, работающее на Node.js 20, не используется.
- GitHub Actions и локальная сборка используют один файл `BUILD_EXE.bat`. Он устанавливает зависимости из `requirements.txt` и явно проверяет `cryptography` и Rust binding.
- Перед публикацией тестируется сам собранный EXE: проверяются наличие пакета и `_rust` в PyInstaller archive, импорт криптографических модулей, Scrypt и AES-GCM round-trip.
- Версия 4.1.5.2 синхронизирована в приложении, updater, Windows version resource и Inno Setup.
- Добавлены проверки переносов строк, BOM и финального перевода строки.

## Порядок выпуска

1. Проверьте ветку `fix/4.1.5.2-verified-build` и Pull Request в `main`.
2. Убедитесь, что workflow **Verify Windows build — 4.1.5.2** завершился успешно: unit-тесты, сборка и smoke-test EXE должны пройти.
3. После слияния ветки в `main` создайте и отправьте тег `v4.1.5.2`.
4. В GitHub Actions должен запуститься **Build Windows release — verified 4.1.5.2**.
5. Скачивайте `MoyaIshka.exe` и `MoyaIshka.exe.sha256` только после успешного шага **Verify frozen EXE imports and encryption round-trip** и успешной публикации.

## Как распознать старый workflow

Если в журнале сборки появляется предупреждение, перечисляющее `actions/checkout@v4`, `actions/setup-python@v5` или `softprops/action-gh-release@v2`, запущена старая конфигурация. Не используйте EXE из такого запуска.

Node.js нужен только внутри GitHub Actions для запуска actions. Устанавливать Node.js на пользовательский компьютер для работы автономного EXE не требуется.
