"""Published presentation settings in an already tenant-scoped transaction."""
import os
from sqlalchemy import text


async def style_for(db,tenant):
    if os.getenv('BUSINESS_PROFILES_ENABLED')!='1':return {}
    row=(await db.execute(text('''SELECT v.id,v.profile FROM business_profile_state s JOIN business_profile_versions v
      ON v.tenant_id=s.tenant_id AND v.id=s.published_version_id WHERE s.tenant_id=:t'''),{'t':tenant})).mappings().one_or_none()
    return {**row['profile'].get('style_rules',{}),'_published_profile_id':str(row['id'])} if row else {}


def presentation_metadata(prepared,settings):
    return {**prepared.metadata(),'source_timezone':settings['timezone'],
        'profile_revision':(settings.get('style') or {}).get('_published_profile_id')}
