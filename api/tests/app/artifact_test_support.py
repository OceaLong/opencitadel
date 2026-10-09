"""Explicit upload-intent test port for existing fake/custom UoW fixtures.

Production DBUnitOfWork composition is exercised separately by U07's owned DB test.
"""


class UnitOfWorkUploadIntents:
    def __init__(self, factory):
        self.factory = factory

    async def register(self, scope, **values):
        async with self.factory() as unit:
            await unit.artifact_provenance.register_upload(scope, **values)
            await unit.commit()
