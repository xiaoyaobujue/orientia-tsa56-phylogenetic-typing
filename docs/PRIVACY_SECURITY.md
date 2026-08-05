# Privacy and security

- The service accepts anonymous research jobs and does not require an account in V1.0.
- Job IDs use random 128-bit identifiers; result files are served only from the resolved job output directory.
- Uploads are limited to expected extensions/content and 8 MB per file.
- The local configuration defaults to one concurrent phylogenetic job with IQ-TREE device-adaptive `-T AUTO` allocation.
- Results and uploaded files expire after 24 hours by default. Cleanup only removes UUID job directories inside validated submission directories.
- API responses set no-sniff, frame-deny, same-origin referrer, restricted permissions and no-store headers.
- Docker Compose binds the website and API to `127.0.0.1` by default. Do not change this to `0.0.0.0` unless local-network exposure is explicitly intended and secured.

The tool is not designed for protected health information. Use pseudonymous study labels and remove direct identifiers before upload. A future network deployment would require a separate security/privacy review; it is not part of the publication release.
