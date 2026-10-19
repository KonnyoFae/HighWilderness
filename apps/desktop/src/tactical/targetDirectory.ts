import type {ObservedContact, ObservationShip} from './FireControlPanel';
import type {TargetCategory, TargetDescriptor, TargetDirectory} from './model';

export const TARGET_CATEGORIES:TargetCategory[] = ['ship','aircraft','missile','shell','bomb'];
export const TARGET_LABELS:Record<TargetCategory,string> = {ship:'舰艇',aircraft:'飞机',missile:'导弹',shell:'炮弹',bomb:'炸弹'};
export const TARGET_PAGE_SIZE = 12;
export interface TargetRow extends TargetDescriptor {
  active:boolean; contact?:ObservedContact; state:'unseen'|'lost'|'tracked'; canLock:boolean;
}
export function targetKey(category:string, id:string|number) {
  return JSON.stringify([category==='ship'||category==='aircraft'?category:'projectile',id]);
}
function descriptor(c:ObservedContact,names:Record<string,string>):TargetDescriptor {
  const category = (TARGET_CATEGORIES as string[]).includes(c.kind) ? c.kind as TargetCategory : 'missile';
  return {key:targetKey(category,c.id),target_id:c.id,category,
    name:category==='ship'?(names[c.id]??'敌舰'):`${TARGET_LABELS[category]} #${c.id}`};
}
// Metadata never grants a contact. All pose, freshness and command eligibility
// come exclusively from this observer's sampled channels, including datalinks.
export function targetRows(directory:TargetDirectory|undefined,ship:ObservationShip|undefined,
  names:Record<string,string>,memorySeconds:number):TargetRow[] {
  const contacts = new Map((ship?.contacts??[])
    .filter(c=>c.valid||c.age_s<=memorySeconds).map(c=>[targetKey(c.kind,c.id),c]));
  const rows:TargetRow[] = [], seen = new Set<string>();
  const append = (d:TargetDescriptor,active:boolean) => {
    if(seen.has(d.key))return;
    seen.add(d.key);
    const contact = contacts.get(d.key);
    rows.push({...d,active,contact,state:!contact?'unseen':contact.valid?'tracked':'lost',
      canLock:!!contact?.valid&&contact.status!=='no_computer'});
  };
  if(directory)for(const entry of directory.entries)append(entry,true);
  const history = new Map(directory?.remembered.map(d=>[d.key,d])??[]);
  for(const [key,c] of contacts)if(!seen.has(key))append(history.get(key)??descriptor(c,names),!directory&&c.valid);
  return rows;
}

export function targetPage(rows:TargetRow[],requested:number) {
  const pages=Math.max(1,Math.ceil(rows.length/TARGET_PAGE_SIZE));
  const page=Math.max(0,Math.min(pages-1,requested));
  return {page,pages,rows:rows.slice(page*TARGET_PAGE_SIZE,(page+1)*TARGET_PAGE_SIZE)};
}
