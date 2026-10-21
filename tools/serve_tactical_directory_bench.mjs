// Build an isolated benchmark with production React, then serve only its output.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root=path.resolve('apps/desktop'),require=createRequire(path.join(root,'package.json'));
const {build}=await import(pathToFileURL(require.resolve('vite')).href);
const out=path.join(root,'dist/target-directory-bench');
await build({root,build:{outDir:out,emptyOutDir:true,rollupOptions:{input:path.join(root,'target-directory-perf.html')}}});
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'};
createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://127.0.0.1:1425'),relative=decodeURIComponent(url.pathname).replace(/^\/+/,''),file=path.resolve(out,relative||'target-directory-perf.html');
    if(!file.startsWith(out+path.sep)){res.writeHead(403);res.end();return;}
    const data=await readFile(file);res.writeHead(200,{'Content-Type':mime[path.extname(file)]??'application/octet-stream'});res.end(data);
  }catch{res.writeHead(404);res.end();}
}).listen(1425,'127.0.0.1',()=>console.log('Production target directory benchmark: http://127.0.0.1:1425'));
