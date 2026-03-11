ZIONA_SERVER

Ziona Server is the backend API for the Ziona social media platform. Built with Django, Strawberry GraphQL, and PostgreSQL, it provides JWT authentication, Google OAuth, media uploads via GCP signed URLs, async email delivery, and Celery background processing — all structured around a clean domain-driven architecture.

 Cloning the Repository

Clone the repository:

```bash
git clone https://github.com/zionkingllc-ship-it/Ziona_Server.git
```

Navigate into the project directory:

```bash
cd Ziona_Server
```

 Project Architecture

```
Ziona_Server/
│
├── config/                              # Django project configuration
│   ├── settings/
│   │   ├── base.py                      # Shared settings across all environments
│   │   ├── dev.py                       # Local development settings
│   │   ├── staging.py                   # Render staging settings
│   │   └── production.py               # GCP Cloud Run production settings
│   ├── celery.py                        # Celery app configuration
│   ├── graphql_schema.py                # Root Strawberry GraphQL schema
│   ├── swagger.py                       # Swagger UI view (OpenAPI docs)
│   ├── urls.py                          # URL routing
│   ├── wsgi.py                          # WSGI entry point
│   └── asgi.py                          # ASGI entry point
│
├── core/                                # Domain-driven application modules
│   ├── authentication/                  # Authentication module
│   │   ├── tokens.py                    # JWT token service (generate, validate, rotate)
│   │   ├── services.py                  # Auth business logic (register, login, verify)
│   │   ├── views.py                     # REST API views
│   │   ├── schema.py                    # GraphQL queries and mutations
│   │   ├── urls.py                      # Auth URL routing
│   │   └── permissions.py               # GraphQL permission classes
│   │
│   ├── users/                           # Users module
│   │   ├── models.py                    # Custom User model (UUID, soft delete, roles)
│   │   ├── managers.py                  # Custom UserManager (email-based auth)
│   │   ├── services.py                  # Username setting, DOB encryption
│   │   ├── selectors.py                 # Read-only data access layer
│   │   ├── schema.py                    # GraphQL types and mutations
│   │   ├── validators.py                # Username format and reserved word validation
│   │   └── admin.py                     # Django admin interface
│   │
│   ├── media/                           # Media upload module
│   │   ├── models.py                    # MediaFile model
│   │   ├── services.py                  # GCP signed URL generation
│   │   ├── schema.py                    # GraphQL mutations for media upload
│   │   └── tasks.py                     # Celery processing tasks
│   │
│   └── shared/                          # Shared utilities
│       ├── models.py                    # Abstract base models (UUID, timestamps, soft delete)
│       ├── middleware.py                # Structured logging + Redis rate limiting
│       ├── logging.py                   # JSON structured logging formatter
│       ├── email_backends/
│       │   └── ensend.py                # Custom Ensend (SMTP Express) email backend
│       └── tasks/
│           └── email_tasks.py           # Celery async email sending task
│
├── tests/                               # Test suite (pytest)
│   ├── authentication/
│   │   ├── test_services.py             # Auth service tests
│   │   ├── test_tokens.py               # JWT token tests
│   │   └── test_views.py                # REST API view tests
│   ├── shared/
│   │   ├── test_ensend_backend.py       # Ensend email backend tests
│   │   └── test_email_tasks.py          # Celery email task tests
│   └── users/
│       └── test_users.py                # User model and service tests
│
├── templates/                           # Email templates
│   └── emails/
│       ├── email_verification.html      # HTML verification email
│       ├── password_reset.html          # HTML password reset email
│       └── text/                        # Plain text variants
│
├── docs/                                # Project documentation
│   └── MILESTONE_1_VALIDATION.md        # M1 validation checklist
│
├── .github/workflows/ci.yml             # GitHub Actions CI/CD pipeline
├── docker-compose.yml                   # Local dev environment (Postgres + Redis)
├── Dockerfile                           # Production container
├── render.yaml                          # Render deployment config
├── requirements.txt                     # Python dependencies
├── pyproject.toml                       # Tool configuration (ruff, pytest, mypy)
├── conftest.py                          # Pytest shared fixtures
├── .env.example                         # Template for environment variables
├── .gitignore                           # Files/folders to ignore in Git
└── manage.py                            # Django management entry point
```

 Setup Instructions

 Prerequisites

- Python 3.12+
- Redis
- PostgreSQL (optional for local — SQLite works in dev)

 Create a virtual environment

```bash
python3 -m venv venv
```

 Activate the virtual environment

On macOS/Linux:

```bash
source venv/bin/activate
```

On Windows (PowerShell):

```powershell
venv\Scripts\Activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

 Create a `.env` file from `.env.example`

```bash
cp .env.example .env
```

Update the `.env` file with your values:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Random secret key for Django |
| `JWT_SECRET_KEY` | Random secret key for JWT signing |
| `DATABASE_URL` | Database connection string |
| `REDIS_URL` | Redis connection string |
| `ENSEND_API_KEY` | Ensend (SMTP Express) API key |
| `DEFAULT_FROM_EMAIL` | Verified sender email address |
| `GCP_STORAGE_BUCKET` | GCP Cloud Storage bucket name |
| `GCP_CREDENTIALS_FILE` | Path to GCP service account JSON |
| `FIREBASE_CREDENTIALS_FILE` | Path to Firebase service account JSON |
| `ENCRYPTION_KEY` | Fernet key for encrypting sensitive fields |
| `SENTRY_DSN` | Sentry error tracking DSN |

Run database migrations

```bash
python manage.py migrate
```

Start the development server

```bash
python manage.py runserver
```

The app will start at `http://localhost:8000`.

Docker (Alternative)

```bash
docker-compose up -d
```

 Starting the Celery Worker

Redis must be running before starting Celery.

 Install and start Redis

```bash
sudo apt-get install redis-server -y
redis-server --daemonize yes
redis-cli ping
```

 Start the worker

```bash
celery -A config worker --loglevel=info
```

 Start the beat scheduler (periodic tasks)

```bash
celery -A config beat --loglevel=info
```

Run each command in a separate terminal window.

 API Endpoints

 REST Authentication (`/api/auth/`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Register with email and password |
| `POST` | `/api/auth/login` | Login, returns JWT tokens |
| `POST` | `/api/auth/refresh` | Rotate refresh token |
| `POST` | `/api/auth/logout` | Revoke tokens |
| `POST` | `/api/auth/verify-email` | Verify email with token |
| `POST` | `/api/auth/password-reset` | Request password reset OTP |
| `POST` | `/api/auth/password-reset/confirm` | Reset password with OTP |
| `POST` | `/api/auth/google` | Google OAuth login |

 GraphQL (`/graphql/`)

```graphql
query { me { id email username role } }
query { health }

mutation { register(email: "...", password: "...") { success accessToken user { id } } }
mutation { login(email: "...", password: "...") { success accessToken refreshToken } }
mutation { refreshToken(refreshToken: "...") { success accessToken refreshToken } }
mutation { setUsername(username: "...") { success user { username } } }
mutation { checkUsernameAvailability(username: "...") { available suggestions } }
mutation { setDateOfBirth(dateOfBirth: "2000-01-01") { success } }
mutation { requestMediaUpload(fileName: "...", fileType: "image/jpeg", fileSize: 1024) { uploadUrl mediaId } }
```

 Other Endpoints

| Endpoint | Description |
|---|---|
| `/health/` | Health check |
| `/admin/` | Django admin panel |
| `/docs/` | Swagger UI (REST API documentation) |
| `/graphql-docs/` | SpectaQL (GraphQL documentation) |
| `/api/schema/` | OpenAPI JSON schema |

 Running Tests

Run all tests:

```bash
pytest
```

Run tests with coverage:

```bash
pytest --cov=core --cov-report=term-missing
```

Run a specific test file:

```bash
pytest tests/authentication/test_tokens.py -v
```

 Code Quality

Check linting with Ruff:

```bash
ruff check .
```

Auto-fix lint errors:

```bash
ruff check . --fix
```

Format code:

```bash
ruff format .
```

 Deployment

- Staging:
 Auto-deploys to Render on merge to `main`
- CI/CD: GitHub Actions runs lint, tests, and security checks on every push

 License

Private — Ziona King LLC
