import { app } from "../../scripts/app.js";

const reasoningModes = new Set(["auto", "off", "on"]);
const seedModes = new Set(["fixed", "increment", "decrement", "randomize"]);
const has = (values, name) => Object.hasOwn(values, name);

function savedValues(node, info, defaultEditMaxTokens) {
    let saved;
    if (info.widgets_values_named && typeof info.widgets_values_named === "object"
        && !Array.isArray(info.widgets_values_named)) {
        saved = { ...info.widgets_values_named };
    } else if (Array.isArray(info.widgets_values)) {
        const values = [...info.widgets_values];
        const widgets = node.widgets || [];
        const index = widgets.findIndex(widget => widget.name === "reasoning");
        // Historical graphs ended with canvas_image before reasoning was added.
        if (index >= 0 && values.length === index + 1 && Number.isInteger(values[index])
            && values[index] >= 1 && values[index] <= 10) values.splice(index, 0, "auto");
        saved = Object.fromEntries(widgets.slice(0, values.length).map((widget, i) => [widget.name, values[i]]));
    } else return;
    if (!has(saved, "reasoning")) saved.reasoning = "auto";
    if (defaultEditMaxTokens !== undefined && !has(saved, "edit_max_tokens")) {
        saved.edit_max_tokens = defaultEditMaxTokens;
    }
    // Native named restoration can save the canvas number into reasoning.
    // Its previous thinking choice is lost; auto keeps image editing fast.
    if (Number.isInteger(saved.reasoning) && saved.reasoning >= 1 && saved.reasoning <= 10
        && saved.reasoning === saved.canvas_image && Number.isInteger(saved.seed)
        && seedModes.has(saved.control_after_generate)) saved.reasoning = "auto";
    if (!reasoningModes.has(saved.reasoning)) {
        throw new Error(`Qwen PE #${node.id}: reasoning must be auto, off or on; got ${JSON.stringify(saved.reasoning)}`);
    }
    return saved;
}

function restore(node, saved) {
    if (!saved) return;
    for (const widget of node.widgets || []) {
        if (has(saved, widget.name)) widget.value = saved[widget.name];
    }
}

app.registerExtension({
    name: "QwenPE.WidgetValues",
    beforeRegisterNodeDef(Node, data) {
        if (data.name !== "QwenPERewriteT8") return;
        const fields = new Set(Object.entries({ ...data.input?.required, ...data.input?.optional })
            .filter(([, spec]) => Array.isArray(spec[0]) || ["INT", "FLOAT", "STRING", "BOOLEAN"].includes(spec[0]))
            .map(([name]) => name));
        fields.add("control_after_generate");
        const defaultEditMaxTokens = data.input?.optional?.edit_max_tokens?.[1]?.default;
        const configure = Node.prototype.configure;
        Node.prototype.configure = function (info) {
            const saved = savedValues(this, info, defaultEditMaxTokens);
            const copy = { ...info };
            if (saved) {
                // Native schema ordering omits the frontend-only seed control.
                // Restore using actual widget names, including converted inputs.
                delete copy.widgets_values_named;
                copy.widgets_values = (this.widgets || []).map(widget =>
                    has(saved, widget.name) ? saved[widget.name] : widget.value);
            }
            const result = configure?.call(this, copy);
            restore(this, saved);
            return result;
        };
        const serialize = Node.prototype.onSerialize;
        Node.prototype.onSerialize = function (info) {
            const result = serialize?.call(this, info);
            info.widgets_values_named = Object.fromEntries((this.widgets || [])
                .filter(widget => fields.has(widget.name) && widget.value !== undefined)
                .map(widget => [widget.name, widget.value]));
            return result;
        };
    },
});
