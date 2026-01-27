# Job Examples Convention

> Every job must have CLI + curl examples in main.py help text.

---

## Rule

When creating a new job, always add examples to the `run` subparser epilog in `main.py`:

```python
  new_job_name (BQ auto-discovery if applicable):
    python main.py run new_job_name --dry-run
    python main.py run new_job_name --dry-run --limit 5
    python main.py run new_job_name --dry-run key_param=value
    curl -X POST {base_url}/execute -H "Content-Type: application/json" \
      -d '{{"job":"new_job_name","dry_run":true,"params":{{"limit":10}}}}'
```

## Requirements

1. **CLI example**: At least one `--dry-run` call with no params (BQ discovery)
2. **CLI example**: One call with the most common parameter
3. **curl example**: One call with `limit` param
4. **Label**: Add `(BQ auto-discovery)` if the job discovers candidates from BigQuery when no IDs are provided

## Location

The `base_url` variable is defined at the top of `cli()` in `main.py`. Use it in curl examples via f-string interpolation.

---

**Last updated**: 2025-01-27
