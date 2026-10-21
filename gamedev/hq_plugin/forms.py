from django import forms

from .contracts import TRANSITIONS


class TaskForm(forms.Form):
    project_slug = forms.ChoiceField(label="Project")
    title = forms.CharField(max_length=500)
    agent = forms.ChoiceField(choices=(("codex", "Codex · primary"), ("opencode", "OpenCode · delegated")))
    parent_id = forms.IntegerField(required=False, min_value=1, label="Codex parent task number (for delegation)")


class MoveForm(forms.Form):
    task_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    status = forms.ChoiceField(choices=[(name, name.title()) for name in TRANSITIONS])
    evidence = forms.CharField(
        max_length=4000, required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Evidence / notes"
    )
