# Обновление репозитория до «Моя ИИшка» 4.1.5

Версия 4.1.5 исправляет сборку `cryptography` в Windows EXE и обновляет GitHub Actions до Node.js 24 runtime. Google Drive остаётся основным облаком, Supabase — резервной копией.

## Обновить исходники

1. Скачайте ZIP-патч 4.1.5 и распакуйте его в корень локальной копии репозитория.
2. Согласитесь на замену файлов. Убедитесь, что появились `hooks/hook-cryptography.py` и `tests/test_build_release.py`.
3. Не удаляйте скрытую папку `.git`.
4. Выполните Commit и Push в ветку `main`.

## Опубликовать 4.1.5

1. Откройте GitHub → Actions → Build Windows release.
2. Нажмите Run workflow.
3. В `release_tag` укажите `v4.1.5`.
4. Дождитесь зелёного результата. Обязательный шаг `Verify frozen EXE crypto imports` проверяет реальный EXE; если cryptography не упакована, публикация не продолжится.
5. В Releases проверьте `MoyaIshka.exe` и `MoyaIshka.exe.sha256`.

В workflow используются `actions/checkout@v7.0.1` и `actions/setup-python@v7.1.0`, работающие на runtime Node.js 24. Старое действие `softprops/action-gh-release@v2` больше не используется; релиз создаётся через `gh` CLI. Node.js на компьютере пользователя не требуется.

## Состав исправления

- Явные импорты `cryptography` в `main.py`.
- Собственный PyInstaller hook `hooks/hook-cryptography.py`.
- Сбор hidden imports, Rust extension, package metadata и бинарников.
- Предварительная проверка окружения сборки.
- Self-test AES-GCM + Scrypt в готовом EXE.
- Проверка тех же импортов на GitHub Actions до публикации релиза.
