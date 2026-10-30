// No npm install or network needed: synthetic lockfile fixtures.
const assert=require('node:assert/strict');
const {mkdtempSync,mkdirSync,writeFileSync,rmSync,readFileSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');

(async () => {
  const {verifyNodeDeps}=await import('../scripts/verify-node-deps.mjs');
  const root=mkdtempSync(join(tmpdir(),'stt-deps-test-'));
  const wrangler={version:'4.156.0',integrity:'sha512-wranglerfixture',resolved:'https://registry.example/wrangler.tgz'};
  const dep={version:'0.1.0',integrity:'sha512-depfixture',resolved:'https://registry.example/dependency.tgz'};
  const optional={version:'0.1.0',integrity:'sha512-optionalfixture',resolved:'https://registry.example/optional.tgz',optional:true};
  const lock={lockfileVersion:3,packages:{
    '':{devDependencies:{wrangler:'^4.0.0'}},
    'node_modules/wrangler':wrangler,
    'node_modules/dep':dep,
    'node_modules/optional':optional
  }};
  const hidden={lockfileVersion:3,packages:{
    'node_modules/wrangler':{...wrangler},
    'node_modules/dep':{...dep}
  }};
  const save=(file,obj)=>writeFileSync(join(root,file),JSON.stringify(obj));
  const ensure=name=>{
    mkdirSync(join(root,name),{recursive:true});
    writeFileSync(join(root,name,'package.json'),JSON.stringify({name,version:'test'}));
  };
  try {
    save('package.json',{name:'test',devDependencies:{wrangler:'^4.0.0'}});
    save('package-lock.json',lock);
    mkdirSync(join(root,'node_modules'),{recursive:true});
    save('node_modules/.package-lock.json',hidden);
    ensure('node_modules/wrangler');
    ensure('node_modules/dep');
    mkdirSync(join(root,'node_modules/wrangler/bin'),{recursive:true});
    writeFileSync(join(root,'node_modules/wrangler/bin/wrangler.js'),'// fixture');
    mkdirSync(join(root,'node_modules/@esbuild/win32-x64'),{recursive:true});
    writeFileSync(join(root,'node_modules/@esbuild/win32-x64/esbuild.exe'),'test');
    const before=readFileSync(join(root,'node_modules/.package-lock.json'),'utf8');
    const report=verifyNodeDeps(root);
    assert.equal(report.checked,2);
    assert.equal(readFileSync(join(root,'node_modules/.package-lock.json'),'utf8'),before,
      'Verification must not modify installed dependencies');
    // Absent incompatible/optional packages are not a reason to reinstall.
    hidden.packages['node_modules/dep'].version='0.2.0';
    save('node_modules/.package-lock.json',hidden);
    assert.throws(()=>verifyNodeDeps(root),/differs from lockfile/);
    hidden.packages['node_modules/dep'].version='0.1.0';
    delete hidden.packages['node_modules/dep'];
    save('node_modules/.package-lock.json',hidden);
    assert.throws(()=>verifyNodeDeps(root),/Missing locked package/);
    hidden.packages['node_modules/dep']={...dep};
    save('node_modules/.package-lock.json',hidden);
    lock.packages[''].devDependencies.wrangler='^5.0.0';
    save('package-lock.json',lock);
    assert.throws(()=>verifyNodeDeps(root),/does not match package-lock/);
  } finally {
    rmSync(root,{recursive:true,force:true});
  }
  console.log('Node dependency reuse: verified, optional packages, drift and incomplete install guarded.');
})().catch(error=>{console.error(error);process.exitCode=1});
