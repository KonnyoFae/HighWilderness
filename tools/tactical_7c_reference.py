"""7c.1a test-only reference, copied from production HEAD 79db27e.

Keep both functions unchanged: they are the independent pre-optimization oracle.
"""


def segment_circle(a,b,center,radius):
    delta=tuple(y-x for x,y in zip(a,b));length=sum(x*x for x in delta)
    t=max(0.,min(1.,sum((c-x)*d for c,x,d in zip(center,a,delta))/length)) if length else 0.
    return sum((x+d*t-c)**2 for x,d,c in zip(a,delta,center))<=radius*radius


def blocked_channels(areas,origin,layer,target,target_layer,*,observer_side=None,seeker=None,target_jamming=False):
    # Only endpoint layers participate: a cloud is not projected through every
    # altitude. Physical clouds affect both sides; electronic areas are hostile-only.
    blocked=set()
    for a in areas:
        if a.layer not in (layer,target_layer) or not segment_circle(origin,target,a.position,a.radius):continue
        if a.kind in ('chaff','thermal'):blocked.add(a.kind)
        elif a.kind=='electronic' and observer_side is not None and a.side!=observer_side:
            # Only active jammer targets bypass electronic fields, including overlap.
            # Actual chaff remains a separate obstacle to anti-radiation seekers.
            if not (seeker=='anti_radiation' and target_jamming):blocked.add('chaff')
    return frozenset(blocked)
