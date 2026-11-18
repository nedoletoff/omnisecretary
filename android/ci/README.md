# CI для Android-клиента

GitHub запускает только файлы из `.github/workflows/`. Этот каталог хранит
готовый workflow рядом с приложением, потому что токен на Pi без scope
`workflow` не может пушить файлы в `.github/`.

Установка (один раз, нужен токен со scope `workflow`):

```bash
./scripts/install-android-ci.sh          # скопировать в .github/workflows/
git add .github/workflows/android.yml
git commit -m "chore: CI-сборка Android APK"
git push
```

Либо вручную: GitHub → Add file → Create new file →
`.github/workflows/android.yml` → вставить содержимое
`android/ci/github-actions-android.yml` → Commit.

После этого каждый push в `android/**` собирает debug APK и кладёт его в
артефакты запуска, а тег `android-v*` создаёт GitHub Release с APK.

## Почему сборка не на Pi

`aapt2` из Android SDK существует только под x86-64 — на Raspberry Pi
(arm64) он падает с `Exec format error`. Поэтому APK собирается на
x86-64-раннере GitHub Actions.
