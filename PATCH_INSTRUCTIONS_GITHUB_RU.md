# Обновление до 4.1.5.1

**ОБЯЗАТЕЛЬНО:** замените `.github/workflows/release.yml` в репозитории. Именно старый workflow вызывает предупреждения Node.js 20 и не проверяет готовый EXE. После Commit/Push в Actions должно появиться имя `Build Windows release — verified 4.1.5.1`.

1. Распакуйте патч в корень локальной копии репозитория с заменой файлов.
2. Проверьте в GitHub → Code, что `.github/workflows/release.yml` содержит имя `Build Windows release — verified 4.1.5.1`.
3. Сделайте Commit и Push в `main`.
4. Создайте и отправьте тег `v4.1.5.1` либо запустите новую workflow вручную с `release_tag=v4.1.5.1`.
5. В Actions дождитесь шага `Verify frozen EXE imports and encryption round-trip`; он должен пройти до публикации релиза.

Если запуск называется просто `Build Windows release` и содержит предупреждение про `checkout@v4`, `setup-python@v5` или `softprops/action-gh-release@v2`, это старый workflow. Не скачивайте EXE из такого запуска.


Если распаковщик пропускает скрытые папки, скачайте `release.yml` отдельно и создайте в GitHub файл `.github/workflows/release.yml`, заменив его содержимое целиком. После загрузки откройте GitHub → Code и убедитесь, что первая строка файла `name: Build Windows release — verified 4.1.5.1`.
