# V2 fixture conventions

Create each catalog, database and Git checkout under the test's temporary directory; never open the owner's catalog. Seed two actor partitions in the same legacy namespace with overlapping text but distinct IDs. Add a separate unmounted canary space; assert that its identity as well as its contents stay out of results and errors.

Build byte-span examples from explicit UTF-8 bytes, including composed and decomposed accents; never normalize before asserting round trips. Generate local main and release commits in a temporary repository and retain their actual OIDs. Use synthetic sources, a fake clock, named fault points and process checkpoints instead of production exports, sleeps, external models or network services.
