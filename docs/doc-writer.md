# Historical document-writer workflow

The former status UI and request database coordinated document drafting, human questions, exact-byte approval, and publication. That controller and its queue have been removed. **Do not run the old doc-writer service or follow its publication and Signal-alert startup instructions:** neither is part of the supported personal Hermes fleet.

The independent [`bin/doc-writer-publication`](../bin/doc-writer-publication) helper remains for exact-byte, no-overwrite file operations; it is not a queue, approval workflow, or permission to publish documents. Any future document-writing workflow needs its own explicit approval boundary and implementation. For active PR automation, see [getting started](getting-started.md).
