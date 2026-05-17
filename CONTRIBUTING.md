# Contributing

Run before submitting changes:

```bash
python -m pytest
python -m compileall src tests
docker build -t kaloricke-tabulky-mcp:test .
docker compose config
```

Do not include real credentials in examples, tests, screenshots, or logs.

