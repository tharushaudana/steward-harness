// Render every README figure, in both themes, into ../assets/.   Usage: node render.mjs [LogoBanner|LogoIcon|Architecture|Dadr|KnowledgeRadial|KnowledgeStrikes ...]
import {execFileSync} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'assets');
mkdirSync(out, {recursive: true});
const cli = join(here, 'node_modules', '@remotion', 'cli', 'remotion-cli.js');

const jobs = [];
for (const theme of ['light', 'dark']) {
  jobs.push(['still', 'LogoBanner', `steward-logo-${theme}.png`, theme]);
  jobs.push(['still', 'Architecture', `architecture-${theme}.png`, theme]);
  jobs.push(['still', 'Dadr', `dadr-${theme}.png`, theme]);
  jobs.push(['still', 'KnowledgeRadial', `knowledge-radial-${theme}.png`, theme]);
  jobs.push(['gif', 'KnowledgeStrikes', `knowledge-strikes-${theme}.gif`, theme]);
}
jobs.push(['still', 'LogoIcon', 'steward-icon.png', 'light']);

const only = process.argv.slice(2);
for (const [kind, id, file, theme] of jobs) {
  if (only.length && !only.includes(id)) continue;
  const props = JSON.stringify({theme});
  const args = kind === 'still'
    ? [cli, 'still', 'src/index.ts', id, join(out, file), `--props=${props}`]
    : [cli, 'render', 'src/index.ts', id, join(out, file), `--props=${props}`, '--codec=gif', '--every-nth-frame=2'];
  console.log(`> ${id} (${theme}) -> assets/${file}`);
  execFileSync(process.execPath, args, {cwd: here, stdio: ['ignore', 'ignore', 'inherit']});
}
console.log('done');
