# RCS Portal

Enterprise multi-role Rich Communication Services (RCS) messaging portal built on Django 5.x and integrated with the OneXtel Aura API engine.

---

## Key Capabilities & Architecture

- **Multi-Role Access Control**: Strictly partitioned roles for **Admin**, **Reseller**, and **Client User**.
- **Role-Scoped Data Security**: Shared querysets (`RoleScopedQuerySetMixin` & `ScopedUserManager`) ensuring users only see their own records, resellers access their direct clients, and administrators oversee platform-wide entities.
- **Modern UI Stack**: Django Server-Rendered Templates + Tailwind CSS + Alpine.js + HTMX.
- **OneXtel Vendor Integration**: Centralized in `integrations/onextel/client.py` with mock mode (`ONEXTEL_MOCK=True`) for safe local testing.
- **Login Defense**: Brute-force lockout protection via `django-axes`.
- **Background Dispatch**: Asynchronous queuing via Celery (eager execution in dev, Redis broker in prod).
- **Split Settings**: Clean separation into `base.py`, `dev.py`, and `prod.py` driven by `.env`.

---

## Directory Structure

```
rcs/
├── config/                  # Core project configuration
│   ├── settings.py          # Unified settings (dev/prod driven by .env)
│   ├── celery.py            # Celery task application setup
│   ├── urls.py              # Root router, API schema, auth redirects
│   └── wsgi.py / asgi.py    # Deployment entrypoints
├── accounts/                # Custom User model, Role scoping mixins, Auth views
├── wallet/                  # Ledger-based wallet accounting
├── templates_mgmt/          # RCS template creation and sync
├── messaging/               # Single message dispatch
├── campaigns/               # CSV bulk campaign dispatch
├── reports/                 # Delivery & analytics reports
├── api/                     # REST API endpoints
├── integrations/            # OneXtel client module & external services
├── templates/               # Responsive HTML templates with role sidebars
├── requirements.txt         # Consolidated Python dependencies
├── tests/                   # Pytest test suite
└── manage.py
```

---

## Quickstart & Run Steps

### 1. Environment Setup

```bash
# Clone and enter directory
cd d:/Antigravity/rcs

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate   # Windows
# source .venv/bin/activate # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the example configuration file:
```bash
cp .env.example .env
```

### 3. Database Migration

Apply initial migrations (creates custom User model first, followed by auth and axes tables):
```bash
python manage.py migrate
```

### 4. Create Demo Data

Seed 1 Admin, 1 Reseller, and 2 Users under that Reseller:
```bash
python manage.py create_demo_data
```

### 5. Start the Development Server

```bash
python manage.py runserver
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in your browser.

---

## Pre-configured Demo Accounts

| Role | Email | Password | Access / Scope |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@rcsportal.com` | `Admin@12345` | Global oversight, all users & resellers |
| **Reseller** | `reseller@acme-messaging.com` | `Reseller@12345` | Reseller Hub, manages own clients |
| **Client User 1** | `user1@alpha-corp.com` | `User1@12345` | Client portal, single & campaign messaging |
| **Client User 2** | `user2@beta-logistics.com` | `User2@12345` | Client portal, isolated data partition |

---

## Running the Automated Test Suite

Run all verification tests using `pytest`:

```bash
pytest
```
