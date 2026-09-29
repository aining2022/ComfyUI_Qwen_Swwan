import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const names = ['user_prompt', 'task', 'aspect_ratio', 'output_language', 'transparent_rgba',
    't2i_model', 'edit_model', 'vision_model', 'model_lifetime', 'seed',
    'control_after_generate', 'reasoning', 'canvas_image', 'edit_max_tokens'];
const values = ['', 'edit', 'auto', 'English', false, 'model.gguf', 'model.gguf', 'vision.gguf',
    'after_run', 42, 'fixed', 'off', 1, 1536];
const data = { name: 'QwenPERewriteT8', input: { required: Object.fromEntries(names
    .filter(name => !['control_after_generate', 'edit_max_tokens'].includes(name)).map(name => [name, ['STRING']])),
    optional: { edit_max_tokens: ['INT', { default: 1536, min: 1, max: 65536 }] } } };
class Node {
    constructor() { this.id = 27; this.widgets = names.map((name, i) => ({ name, value: values[i] })); }
    configure(info) {
        // Reproduce native restoration excluding the seed's frontend control.
        const restored = info.widgets_values_named
            ? names.filter(name => name !== 'control_after_generate').map(name => info.widgets_values_named[name])
            : info.widgets_values;
        restored?.forEach((value, i) => { if (this.widgets[i] && value !== undefined) this.widgets[i].value = value; });
        this.inputs = info.inputs;
    }
}
const named = Object.fromEntries(names.map((name, i) => [name, values[i]]));
const baseline = new Node();baseline.configure({ widgets_values_named: named });
assert.equal(baseline.widgets.find(widget => widget.name === 'reasoning').value, 1);
const extensions = [];
const app = { registerExtension: extension => extensions.push(extension) };
const source = readFileSync(new URL('../web/js/widget_values.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '');
vm.runInNewContext(source, { app });
extensions[0].beforeRegisterNodeDef(Node, data);
const value = (node, name) => node.widgets.find(widget => widget.name === name).value;
for (const reasoning of ['auto', 'off', 'on']) {
    const node = new Node();const info = { widgets_values_named: { ...named, reasoning, edit_max_tokens: 8192 }, widgets_values: [...values] };
    node.configure(info);
    assert.equal(value(node, 'reasoning'), reasoning);assert.equal(value(node, 'canvas_image'), 1);
    assert.equal(info.widgets_values_named.reasoning, reasoning); // Do not mutate source data.
    const saved = {};node.onSerialize(saved);
    const loaded = new Node();loaded.configure(saved);
    assert.equal(value(loaded, 'reasoning'), reasoning);assert.equal(value(loaded, 'control_after_generate'), 'fixed');
    assert.equal(value(loaded, 'edit_max_tokens'), 8192);
}
const legacy = new Node();const old = values.filter((_, i) => !['reasoning', 'edit_max_tokens'].includes(names[i]));
legacy.configure({ widgets_values: old });assert.equal(value(legacy, 'reasoning'), 'auto');
assert.equal(value(legacy, 'canvas_image'), 1);assert.equal(value(legacy, 'edit_max_tokens'), 1536);
for (const info of [{ widgets_values: values.slice(0, -1) },
                    { widgets_values_named: Object.fromEntries(Object.entries(named).filter(([name]) => name !== 'edit_max_tokens')) }]) {
    const node = new Node();node.widgets.find(w => w.name === 'edit_max_tokens').value = 8192;
    node.configure(info);
    assert.equal(value(node, 'edit_max_tokens'), 1536);assert.equal(value(node, 'reasoning'), 'off');
}
const corrupted = new Node();corrupted.configure({ widgets_values_named: { ...named, reasoning: 1 } });
assert.equal(value(corrupted, 'reasoning'), 'auto');
assert.throws(() => new Node().configure({ widgets_values_named: { ...named, reasoning: 2 } }), /reasoning must/);
assert.throws(() => new Node().configure({ widgets_values_named: { ...named, reasoning: 'invalid' } }), /reasoning must/);
const connected = new Node();const inputs = [{ name: 'user_prompt', widget: { name: 'user_prompt' }, link: 62 },
    { name: 'edit_max_tokens', widget: { name: 'edit_max_tokens' }, link: 63 }];
connected.configure({ widgets_values_named: named, inputs });assert.equal(connected.inputs, inputs);
const unrelated = class {};
extensions[0].beforeRegisterNodeDef(unrelated, { name: 'OtherNode' });assert.equal(unrelated.prototype.configure, undefined);
console.log('PASS: native seed-control shift, named/positional reload, corrupted canvas alias, explicit on/off, input links, edit token budget/default compatibility, node isolation');
