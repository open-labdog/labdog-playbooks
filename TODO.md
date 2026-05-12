# TODO

Open tasks and forward-looking design notes for `labdog-playbooks`.

## Convention: open-only

Only open items belong in this file. When a task is completed, land
the fix and delete the entry from this file in the same commit. The
commit message is the canonical record (what changed, why, how).

To retrace a completed task, search the commit log:

```
git log --grep "alloy"
git log -- actions/alloy-install/
```

---

## alloy-install

- [ ] **Inject `labdog_group` labels via alloy templates.** Today
      Alloy metrics carry only `instance` (the hostname) plus per-job
      static labels. Operators have no way to slice or alert by
      LabDog group membership. Add a way for the alloy templates to
      render one `labdog_group="<name>"` label per group the host
      belongs to (or a single `labdog_groups="comma,sep,list"` if a
      single label-per-host is preferred — pick whichever Mimir
      handles cleanly under cardinality budgets). Likely needs the
      LabDog action orchestrator to inject `labdog_group_membership`
      (a list of group names) as an Ansible extra-var, then a
      `prometheus.relabel` pipeline component in the base templates
      that adds those labels and gets wired into every scrape job's
      `forward_to`.

- [ ] **Bundle minimal example group configs.** The `group_configs/`
      extension point is in place but the `all/` and `linux/`
      directories ship only README placeholders. Add one
      demonstrative `.alloy.j2` per directory (e.g. site-wide Alloy
      logging level for `all/`; per-systemd-unit metrics scrape for
      `linux/`) so operators have a working pattern to copy rather
      than starting from a blank dir.

- [ ] **Document the `alloy_custom_services` extension point.** The
      role-alloy-linux-services iterates `alloy_custom_services` (a
      `name → port` dict) and renders `service.alloy.j2` per entry,
      but the default is `{}` and there's no example anywhere. Add a
      commented-out example block to
      `roles/role-alloy-linux-services/defaults/main.yml` showing the
      schema, plus a paragraph in `actions/alloy-install/README.md`
      pointing at it.
