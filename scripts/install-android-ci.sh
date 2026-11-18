#!/usr/bin/env bash
# Устанавливает workflow Android-сборки в .github/workflows/.
# Нужен git-токен со scope `workflow`: без него GitHub отклоняет push
# файлов из .github/. Скрипт только копирует файл и коммитит локально.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/android/ci/github-actions-android.yml"
DST="$ROOT/.github/workflows/android.yml"

[[ -f "$SRC" ]] || { echo "[fail] нет $SRC" >&2; exit 1; }

mkdir -p "$ROOT/.github/workflows"
cp "$SRC" "$DST"
echo "[ok] $DST"

cd "$ROOT"
git add .github/workflows/android.yml
git commit -q -m "chore: CI-сборка Android APK в GitHub Actions"
echo "[ok] коммит создан"
echo
echo "Дальше: git push origin main"
echo "Если GitHub отвечает 'refusing to allow a Personal Access Token to create"
echo "or update workflow', у токена нет scope workflow — добавьте его или"
echo "создайте файл через веб-интерфейс GitHub."
