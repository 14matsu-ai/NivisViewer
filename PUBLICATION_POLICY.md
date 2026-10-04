# Publication content policy

Publish only application source, required resources, build and verification
tools, dependency information, user documentation and license/provenance notices.
Keep internal work instructions, personal environment records, validation reports,
review archives, local backups, user media and diagnostic outputs private.

Remove personal information from every published revision and artifact.
Use relative paths or masked examples such as `C:/Users/User/` when a path is
needed. Never publish personal usernames, directories, contact details or user
profiles. Preserve third-party copyrights and required license notices.

Allow screenshots individually by exact filename after reviewing visible text,
image content and metadata. Other screenshots remain excluded from publication.

`scripts/publication_policy.py` defines the publication exclusions and permitted
documentation. Run `scripts/check_docs_privacy.py` before building or publishing,
and inspect the complete source and binary archives, including generated caches.
Git publication and archive publication require their own explicit authorization.

Provide source and required build material matching each distributed binary.
An archived internal worktree is not a release source package. Removing a release
does not remove its tag or historical source. History cleanup must cover all
published references; removal of cached GitHub views may require server support.
