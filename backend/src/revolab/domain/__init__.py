"""REvoLab Core domain package.

The leaf domain modules (`scientific_object`, `provenance`, `knowledge`, and the
shared `persistence` primitives) must not transitively load Identity, so this
package deliberately imports nothing from the Identity domain. Import concrete
symbols from their owning submodule (`revolab.domain.identity.can_mutate`,
`revolab.domain.grants.MutationGrant`, `revolab.domain.errors.*`).
"""
