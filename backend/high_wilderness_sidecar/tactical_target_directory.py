"""Self-contained display metadata, projected only from committed roster rows.

This cache owns JSON values, never entity references or observation samples.
Consumers treat returned values as read-only, like other published snapshots.
"""
import json

LABELS = dict(ship='舰艇', aircraft='飞机', missile='导弹', shell='炮弹', bomb='炸弹')


def key(category, identity):
    family = category if category in ('ship', 'aircraft') else 'projectile'
    return json.dumps([family, identity], ensure_ascii=False, separators=(',', ':'))


def describe(entry):
    return dict(key=key(entry.category, entry.handle.target_id), target_id=entry.handle.target_id,
                name=entry.name, category=entry.category)


class Directory:
    def __init__(self):
        self.frame = None
        self.metadata = {}
        self.entries = ()
        self.value = None

    def view(self, frame, side, observation, names):
        if self.frame is not frame:
            entries = tuple(describe(row) for row in frame.query(exclude_side=side))
            if entries != self.entries:
                self.entries = entries
            self.frame = frame
        current = {row['key']: row for row in self.entries}
        remembered = {}
        for ship in observation['ships']:
            for contact in ship['contacts']:
                identity = key(contact['kind'], contact['id'])
                if identity in current or identity in remembered:
                    continue
                row = self.metadata.get(identity)
                if row is None:
                    category = contact['kind']
                    name = names.get(contact['id']) if category == 'ship' else None
                    row = dict(key=identity, target_id=contact['id'], category=category,
                               name=name or f"{LABELS.get(category, '目标')} #{contact['id']}")
                remembered[identity] = row
        history = tuple(remembered[k] for k in self.metadata if k in remembered) + tuple(
            row for k, row in remembered.items() if k not in self.metadata)
        if self.value is None or self.value['entries'] != self.entries or self.value['remembered'] != history:
            self.value = dict(interface='gaotian.target-directory/v1', epoch=frame.epoch,
                              revision=1 if self.value is None else self.value['revision'] + 1,
                              entries=self.entries, remembered=history)
        # Retain only active entries and metadata still backed by a legal contact.
        self.metadata = {**current, **{row['key']: row for row in history}}
        return self.value
