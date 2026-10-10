"""Frozen V0.1 SQLite schema; never import live model metadata here."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("""CREATE TABLE dataset_heads (
	dataset VARCHAR NOT NULL, 
	version_id VARCHAR NOT NULL, 
	PRIMARY KEY (dataset)
)""")
    op.execute("""CREATE TABLE dataset_versions (
	id VARCHAR NOT NULL, 
	dataset VARCHAR NOT NULL, 
	manifest JSON NOT NULL, 
	coverage JSON NOT NULL, 
	created_at VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	batch_id VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE TABLE jobs (
	id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	payload JSON NOT NULL, 
	snapshot JSON NOT NULL, 
	progress FLOAT, 
	cancel_requested BOOLEAN, 
	heartbeat VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	error TEXT, 
	result JSON, 
	attempt INTEGER, 
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE TABLE presets (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	definition JSON NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE TABLE rule_definitions (
	id VARCHAR NOT NULL, 
	version VARCHAR NOT NULL, 
	definition JSON NOT NULL, 
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE TABLE settings (
	"key" VARCHAR NOT NULL, 
	value JSON NOT NULL, 
	PRIMARY KEY ("key")
)""")
    op.execute("""CREATE TABLE job_logs (
	id INTEGER NOT NULL, 
	job_id VARCHAR NOT NULL, 
	at VARCHAR NOT NULL, 
	level VARCHAR NOT NULL, 
	message TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
)""")
    op.execute("""CREATE TABLE sync_checkpoints (
	job_id VARCHAR NOT NULL, 
	"key" VARCHAR NOT NULL, 
	version_id VARCHAR NOT NULL, 
	PRIMARY KEY (job_id, "key"), 
	FOREIGN KEY(job_id) REFERENCES jobs (id), 
	FOREIGN KEY(version_id) REFERENCES dataset_versions (id)
)""")


def downgrade() -> None:
    op.drop_table("sync_checkpoints")
    op.drop_table("job_logs")
    op.drop_table("settings")
    op.drop_table("rule_definitions")
    op.drop_table("presets")
    op.drop_table("jobs")
    op.drop_table("dataset_versions")
    op.drop_table("dataset_heads")
