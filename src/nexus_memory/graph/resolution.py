"""Append-only resolution decisions; the mutable lookup is only a projection."""
from nexus_memory.domain.errors import InvalidRelation, ResolutionConflict


def canonical(db, space, member):
    seen = set()
    while member not in seen:
        seen.add(member)
        row = db.execute('SELECT target FROM graph_alias_index WHERE space_id=? AND member=?',(space,member)).fetchone()
        if not row:
            return member
        member = row[0]
    raise InvalidRelation('cyclic resolution')


def apply(db, space, seq, change):
    def exists(member):
        return db.execute('SELECT 1 FROM graph_entities WHERE space_id=? AND id=?',(space,member)).fetchone()
    if not db.execute('UPDATE graph_generations SET resolution=resolution+1 WHERE space_id=? AND resolution=?',(space,change.expected_generation)).rowcount:
        raise ResolutionConflict('resolution generation changed')
    if not exists(change.target) or len(change.memberships) != len(set(change.memberships)):
        raise InvalidRelation('invalid resolution target or memberships')
    for member in change.memberships:
        if change.action in ('alias','merge'):
            if bool(exists(member)) != (change.action=='merge') or member == change.target or canonical(db,space,member) != member:
                raise InvalidRelation('invalid resolution member')
            db.execute('INSERT INTO graph_alias_index VALUES(?,?,?,?)',(space,member,change.target,change.action))
            canonical(db,space,member)  # reject cycles before committing
        else:
            action = 'merge' if change.action=='split' else 'alias'
            if not db.execute('DELETE FROM graph_alias_index WHERE space_id=? AND member=? AND target=? AND action=?',(space,member,change.target,action)).rowcount:
                raise InvalidRelation('membership not present')
    db.execute('INSERT INTO graph_resolution_events VALUES(?,?,?,?)',(space,change.expected_generation+1,seq,change.to_json()))
