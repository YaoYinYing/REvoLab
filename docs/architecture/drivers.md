# Drivers

Drivers own provider-specific vocabulary and external capabilities. Core knows only typed protocols and stable references.

The initial registry is application-scoped, uses explicit registration handles, rejects collisions, and can discover optional implementations through `importlib.metadata` entry points. Lifecycle is deliberately small:

```text
DISCOVERED → VALIDATED → STARTED → READY → STOPPED
```

Driver failures are explicit. There are no silent provider fallbacks. The registry does not own a database table and does not duplicate provider execution state. REvoComputeDriver, REvoDesignDriver, and OpenBioDriver remain future integrations until supported contracts are available.
