name: Update writings

on:
  schedule:
    - cron: "0 10 * * 1"
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: update-writings
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Search & render writings
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python scripts/update_writings.py

      - name: Commit & push if anything changed
        run: |
          git config user.name "writings-bot"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          mkdir -p /tmp/gen
          cp data/writings.json /tmp/gen/writings.json
          cp press.html /tmp/gen/press.html
          published=0
          for i in 1 2 3 4 5; do
            git fetch origin main
            git reset --hard FETCH_HEAD
            cp /tmp/gen/writings.json data/writings.json
            cp /tmp/gen/press.html press.html
            git add data/writings.json press.html
            if git diff --staged --quiet; then
              echo "No changes to publish."; published=1; break
            fi
            git commit -m "Auto-update writings ($(date -u +%Y-%m-%d))"
            if git push origin HEAD:main; then
              echo "Published."; published=1; break
            fi
            echo "Push race detected, retrying ($i)..."; sleep 3
          done
          if [ "$published" != "1" ]; then
            echo "::error::Failed to publish after retries"; exit 1
          fi
