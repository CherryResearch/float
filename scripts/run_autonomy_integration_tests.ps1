param(
  [string]$Model = $env:FLOAT_AUTONOMY_TEST_MODEL,
  [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

if (-not $env:OPENAI_API_KEY) {
  throw "OPENAI_API_KEY must be set before running the autonomy integration suite."
}

if ($Model) {
  $env:FLOAT_AUTONOMY_TEST_MODEL = $Model
}
$env:FLOAT_RUN_AUTONOMY_INTEGRATION_TESTS = "1"
$env:FLOAT_AUTONOMY_TEST_TIMEOUT = [string]$TimeoutSeconds

poetry run pytest -q backend/app/tests/integration/test_autonomy_container_orchestration.py
