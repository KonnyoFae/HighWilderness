"""Upgrade idle saved ships once; active/unsaved battles keep exact bindings."""
from datetime import datetime
from .preparation_ammunition_upgrade import upgrade

def policy_upgrade(policy):
    if policy.get('maneuver_thrust_revision')==3:return None
    policy.update(maneuver_thrust_revision=3,id=policy['id']+'.maneuver-v3',version=policy['version']+1)
    return policy

def apply_idle(store,root):
    backup=store.directory/'maneuver-upgrades'/f'before-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    return upgrade(store,root,apply=True,backup_path=backup,policy_upgrade=policy_upgrade,skip_unavailable=True)
