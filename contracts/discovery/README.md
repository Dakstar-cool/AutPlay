# Release discovery contracts

`v1/` contains the accepted Post-MVP A1A provider-neutral application/Web contract. The artifacts
are executable design inputs; no provider, Web route, persistence migration or worker is implemented
or activated by their presence.

The schemas deliberately omit owner input. Runtime derives owner from the authenticated Web actor
and revalidates it at every application and worker boundary.
