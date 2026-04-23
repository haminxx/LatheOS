SHELL := /usr/bin/env bash

.PHONY: help check eval build-x86 build-aarch64 iso iso-aarch64 usb-image release switch lint-daemon fetch-base flash

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "} {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: ## Flake check (evaluates without building)
	nix flake check --no-build --show-trace

eval: ## Show derivation path for x86_64 build
	nix eval .#nixosConfigurations.latheos-x86_64.config.system.build.toplevel.drvPath

build-x86: ## Build the x86_64 system closure (not the ISO)
	nix build .#nixosConfigurations.latheos-x86_64.config.system.build.toplevel

build-aarch64: ## Build the aarch64 system closure (requires binfmt or native ARM)
	nix build .#nixosConfigurations.latheos-aarch64.config.system.build.toplevel

iso: ## Build the x86_64 LatheOS installer ISO
	./scripts/build-latheos-iso.sh

iso-aarch64: ## Build the aarch64 LatheOS installer ISO
	ARCH=aarch64 ./scripts/build-latheos-iso.sh

usb-image: ## Build the flashable USB image (Linux host, sudo required)
	sudo ./scripts/build-usb-image.sh

prefetch: ## Pre-bake AI models into dist/prefetch/ (Ollama + Piper + Whisper + OWW)
	./scripts/prefetch-models.sh

prefetch-big: ## Same as prefetch but also pulls Codestral 22B (~22 GB total)
	HEAVY=big ./scripts/prefetch-models.sh

release: prefetch usb-image ## Full offline-ready USB bundle (prefetch + image + zip)
	@ls -lh dist/latheos-usb.zip dist/latheos-usb.img

shell-dev: ## Run the embedded shell locally (dev install)
	cd platform/embedded-shell && \
	  python3 -m venv .venv && . .venv/bin/activate && \
	  pip install -e . && lathe --color

fetch-base: ## Download the upstream NixOS minimal ISO (dual-install path)
	./scripts/fetch-nixos-base.sh

flash: ## Flash an ISO to a USB (usage: make flash ISO=path DEV=/dev/sdX)
	./scripts/flash-usb.sh $(ISO) $(DEV)

switch: ## Switch the current host to the latheos config (destructive)
	sudo nixos-rebuild switch --flake .#latheos-x86_64

lint-daemon: ## Ruff lint for the local daemon + camctl
	ruff check daemon/cam_daemon daemon/camctl
	ruff format --check daemon/cam_daemon daemon/camctl
