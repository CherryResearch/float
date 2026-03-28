# Open Source License Tracker

Purpose: track every open source license used by Float (runtime, dev, and bundled services). This is a living document; complete before launch.

## Project license
- Float repository: GNU Affero General Public License v3.0 only (see `LICENSE`).
- External contribution ownership is governed separately by `CLA.md` and
  `CONTRIBUTOR_ASSIGNMENT_AGREEMENT.md`.

## Python (Poetry) direct dependencies
| Package | Version | License | Notes |
| --- | --- | --- | --- |
| fastapi | ^0.116.1 | TBD | runtime |
| uvicorn | ^0.34.3 | TBD | runtime |
| pydantic | ^2.11.7 | TBD | runtime |
| sqlalchemy | ^2.0.41 | TBD | runtime |
| psycopg2-binary | ^2.9.10 | TBD | runtime |
| cryptography | ^45.0.4 | TBD | runtime |
| celery | ^5.5.3 | TBD | runtime |
| redis | ^6.2.0 | TBD | optional (workers) |
| python-dotenv | ^1.1.0 | TBD | runtime |
| httpx | ^0.28.1 | TBD | runtime |
| requests | ^2.32.4 | TBD | runtime |
| pandas | ^2.3.0 | TBD | runtime |
| networkx | ^3.1 | TBD | runtime |
| bcrypt | ^4.3.0 | TBD | runtime |
| pyjwt | ^2.10.1 | TBD | runtime |
| pyopenssl | ^25.1.0 | TBD | runtime |
| weaviate-client | ^4.9.2 | TBD | runtime |
| alembic | ^1.16.2 | TBD | runtime |
| beautifulsoup4 | ^4.13.4 | TBD | runtime |
| watchdog | ^6.0.0 | TBD | runtime |
| python-docx | ^1.2.0 | TBD | runtime |
| PyPDF2 | ^3.0.1 | TBD | runtime |
| sentence-transformers | ^5.0.0 | TBD | runtime |
| kokoro | ^0.9.4 | TBD | runtime |
| kittentts | ^0.1.3 | TBD | runtime |
| soundfile | ^0.12.1 | TBD | runtime |
| mcp | ^1.12.2 | TBD | runtime (fastmcp extra) |
| langextract | ^1.0.2 | TBD | runtime |
| transformers | ^4.55.0 | TBD | runtime |
| huggingface-hub | ^0.34.3 | TBD | runtime (hf-xet, hf-transfer extras) |
| openai-harmony | 0.0.3 | TBD | runtime |
| icalendar | ^5.0.11 | TBD | runtime |
| python-dateutil | ^2.9.0 | TBD | runtime |
| pywebpush | ^1.14.0 | TBD | runtime |
| python-json-logger | ^2.0.7 | TBD | optional (telemetry) |
| opentelemetry-exporter-otlp | ^1.27.0 | TBD | optional (telemetry) |
| opentelemetry-instrumentation-fastapi | ^0.48b0 | TBD | optional (telemetry) |
| opentelemetry-instrumentation-requests | ^0.48b0 | TBD | optional (telemetry) |
| opentelemetry-instrumentation-logging | ^0.48b0 | TBD | optional (telemetry) |
| prometheus-client | ^0.20.0 | TBD | optional (telemetry) |
| flower | ^2.0.1 | TBD | optional (workers) |
| scikit-learn | ^1.5.0 | TBD | runtime |
| Pillow | ^10.4.0 | TBD | runtime |
| hf-transfer | ^0.1.9 | TBD | runtime |
| chromadb | ^1.1.0 | TBD | runtime |
| mcp-server | ^0.1.4 | TBD | runtime |
| accelerate | ^1.10.1 | TBD | runtime |

## Python dev dependencies
| Package | Version | License | Notes |
| --- | --- | --- | --- |
| pytest | ^8.4.0 | TBD | dev |
| pytest-asyncio | ^1.0.0 | TBD | dev |
| pre-commit | ^4.2.0 | TBD | dev |

## Frontend runtime dependencies
| Package | Version | License | Notes |
| --- | --- | --- | --- |
| react | ^18.3.1 | TBD | runtime |
| react-dom | ^18.3.1 | TBD | runtime |
| react-router-dom | ^7.1.3 | TBD | runtime |
| axios | ^1.7.9 | TBD | runtime |
| dompurify | ^3.1.6 | TBD | runtime |
| d3 | ^7.9.0 | TBD | runtime |
| marked | ^15.0.6 | TBD | runtime |
| livekit-client | ^2.15.4 | TBD | runtime |
| @livekit/components-react | ^2.9.14 | TBD | runtime |
| @livekit/components-styles | ^1.1.6 | TBD | runtime |
| @mui/material | ^5.15.15 | TBD | runtime |
| @mui/icons-material | ^5.15.15 | TBD | runtime |
| @emotion/react | ^11.11.1 | TBD | runtime |
| @emotion/styled | ^11.11.0 | TBD | runtime |

## Frontend dev dependencies
| Package | Version | License | Notes |
| --- | --- | --- | --- |
| vite | ^6.0.5 | TBD | dev |
| @vitejs/plugin-react | ^4.3.4 | TBD | dev |
| eslint | ^9.17.0 | TBD | dev |
| @eslint/js | ^9.17.0 | TBD | dev |
| eslint-plugin-react | ^7.37.2 | TBD | dev |
| eslint-plugin-react-hooks | ^5.0.0 | TBD | dev |
| eslint-plugin-react-refresh | ^0.4.16 | TBD | dev |
| globals | ^15.14.0 | TBD | dev |
| @types/react | ^18.3.18 | TBD | dev |
| @types/react-dom | ^18.3.5 | TBD | dev |
| @testing-library/react | ^16.0.1 | TBD | dev |
| @testing-library/jest-dom | ^6.5.0 | TBD | dev |
| vitest | ^1.6.0 | TBD | dev |
| jsdom | ^24.0.0 | TBD | dev |

## Runtime services and external components (verify licenses)
- LiveKit server and SDKs
- Pipecat (and any plugins used for turn detection)
- Chroma
- Weaviate
- Celery + Redis
- Hugging Face model artifacts downloaded into `data/models/` (legacy `models/` folders may still appear during migration)
- espeak-ng (system dependency for Kokoro TTS on some platforms)

## Audit workflow (to complete before launch)
1. Python: run `poetry run pip-licenses --format=markdown --with-urls` and paste results into this doc.
2. Frontend: run `npx license-checker --production --json` (or equivalent) and summarize licenses here.
3. Validate any bundled binaries or model weights with their upstream licenses.
4. Add a "reviewed_on" date for each section once verified.
