"""Version existing workflow drafts without enabling execution."""
from alembic import op

revision='0028_workflow_drafts'
down_revision='0027_unified_inbox'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("ALTER TABLE public.workflow_definitions ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0)")
    op.execute("ALTER TABLE public.workflow_definitions ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.workflow_definitions) THEN
      RAISE EXCEPTION 'Preserve versioned workflow drafts before downgrade'; END IF; END $$""")
    op.execute('ALTER TABLE public.workflow_definitions DROP COLUMN updated_at,DROP COLUMN version')
