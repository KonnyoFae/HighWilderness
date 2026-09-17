import type {TacticalView} from './model';
import {FireList} from './FireList';

export function FireSummary({view}:{view:TacticalView}) {
  const gunnery=view.snapshot.gunnery;
  if(!gunnery?.fireproof)return null;
  return <section aria-label="交战防火与火情"><h3>防火与火情</h3>
    <p>防火降低点燃和蔓延概率；已有火情需要损管灭火。橙色火点表示舰外，红色表示舰内。</p>
    {gunnery.fireproof.map(s=>{
      const ship=view.geometry.ships.find(v=>v.id===s.ship_id);
      return <article key={s.ship_id}><h4>{ship?.name??s.ship_id}</h4>
        <p>{s.decks.map(d=>`第 ${d.deck_level} 甲板：${d.multiplier<1?`起火概率降低 ${((1-d.multiplier)*100).toFixed(0)}%`:'无额外防火'}`).join(' · ')}</p>
        <FireList fires={gunnery.damage_control?.fires.filter(f=>f.ship_id===s.ship_id)??[]} name={id=>ship?.modules.find(m=>m.id===id)?.name??id}/>
      </article>;
    })}
  </section>;
}
