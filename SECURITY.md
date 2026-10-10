# Security policy

This project handles identity documents and face images, so security reports get priority.

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this repository
(private vulnerability reporting). Do not open a public issue, and do not include real people's ID images or
personal data in the report. Synthetic examples are enough.

You can expect an acknowledgement within 7 days. Once a fix is released, you will be credited in the changelog
unless you prefer otherwise.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: the library, the server (API, capture page, webhooks, storage, encryption), the Docker image and the
compose files in this repository.

Out of scope: weaknesses of the underlying models (for example a spoof that fools the liveness model) unless the
code makes them worse; those are welcome as regular issues. Deployments you do not own.

## Operator responsibilities

Self-hosters are responsible for TLS, keeping `DATA_ENCRYPTION_KEY` secret and backed up, user consent under
UU PDP, and updating to new releases.
