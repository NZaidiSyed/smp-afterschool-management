/* Exclusive workspace navigation; existing page buttons retain their own behavior. */
(() => {
  const nav=document.getElementById('studentTabs');
  if(!nav)return;
  const headings=[...nav.querySelectorAll('[data-workspace]')];
  const pages=[...nav.querySelectorAll('.tab[data-tab]')];
  const remembered=new Map();
  const groupFor=button=>button.closest('.tab-group');
  const options=heading=>[...document.getElementById(heading.getAttribute('aria-controls')).querySelectorAll('.tab')].filter(button=>!button.hidden&&button.style.display!=='none');
  let selected='operations';

  function sync(){
    headings.forEach(heading=>{heading.hidden=options(heading).length===0;});
    const active=pages.find(button=>button.classList.contains('active'));
    if(active&&active.style.display!=='none'&&!active.hidden){
      const group=groupFor(active);
      const heading=headings.find(h=>h.getAttribute('aria-controls')===group.id);
      selected=heading.dataset.workspace;remembered.set(selected,active.dataset.tab);
    }else{
      const heading=headings.find(h=>h.dataset.workspace===selected&&!h.hidden)||headings.find(h=>!h.hidden);
      if(heading){options(heading)[0]?.click();return;}
    }
    headings.forEach(heading=>{
      const current=heading.dataset.workspace===selected&&!heading.hidden;
      heading.classList.toggle('selected',current);
      heading.setAttribute('aria-selected',String(current));heading.tabIndex=current?0:-1;
      document.getElementById(heading.getAttribute('aria-controls')).hidden=!current;
    });
  }

  function choose(heading){
    const allowed=options(heading);
    const target=allowed.find(button=>button.dataset.tab===remembered.get(heading.dataset.workspace))||allowed[0];
    if(target&&!target.classList.contains('active'))target.click();
    sync();
  }
  headings.forEach(heading=>{
    heading.addEventListener('click',()=>choose(heading));
    heading.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const visible=headings.filter(h=>!h.hidden),index=visible.indexOf(heading);
      const next=event.key==='Home'?visible[0]:event.key==='End'?visible.at(-1):visible[(index+(event.key==='ArrowRight'?1:-1)+visible.length)%visible.length];
      next?.focus();if(next)choose(next);
    });
  });
  // Also follow setup redirects, attendance links and role-driven visibility changes.
  new MutationObserver(records=>{
    if(records.some(record=>record.target.matches('.tab')))sync();
  }).observe(nav,{subtree:true,attributes:true,attributeFilter:['class','style','hidden']});
  sync();
})();
