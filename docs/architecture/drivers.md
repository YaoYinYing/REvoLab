# Drivers (superseded)

> **Superseded by the accepted design.** This bootstrap-era driver description
> (including the 5-state lifecycle `DISCOVERED → VALIDATED → STARTED → READY →
> STOPPED`) is replaced by `docs/architecture/PROVIDER_CAPABILITIES.md` and
> **ADR-0012**, which collapses lifecycle to 2 domain-visible states
> (REGISTERED/READY), splits Driver into lifecycle + per-kind Capability realization,
> and adds the credential model. The no-silent-fallback and no-duplicated-state rules
> here remain in force. Where this file conflicts with the current documents or ADRs,
> the current documents win.
