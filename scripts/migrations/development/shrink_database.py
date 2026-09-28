"""Shrink a development database by removing attachments and product images."""

from logging import getLogger

LOGGER = getLogger("migrations.shrink_database")

env = env  # noqa: F821 # pylint: disable=undefined-variable # pyright: ignore

LOGGER.info("Shrinking Database")
att = env["ir.attachment"].search(
    [
        "|",
        "|",
        "|",
        ("name", "=ilike", "%.pdf"),
        ("name", "=ilike", "%.eml"),
        ("name", "=ilike", "%.zip"),
        ("name", "=ilike", "%.jpg"),
    ]
)
LOGGER.info("Attachments to Delete: %d", len(att))
LOGGER.info("Size: %dMB", round(sum([a.file_size for a in att]) / 1024 / 1024, 2))


chunk_size = 100
for offset in range(0, len(att), chunk_size):
    chunk = att[offset : offset + chunk_size]
    LOGGER.info("Deleting chunk %d of %d", offset // chunk_size + 1, int(len(att) / chunk_size))
    chunk.unlink()
    env.cr.commit()


prods = env["product.template"].search([("image_1920", "!=", False)])
LOGGER.info("Removing Product images for %d products", len(prods))
prods.write({"image_1920": False})
env.cr.commit()

env["ir.autovacuum"]._run_vacuum_cleaner()
env.cr.commit()
