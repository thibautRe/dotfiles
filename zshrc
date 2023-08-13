#!/bin/sh

# ---- Oh My Zsh ----

export ZSH="$HOME/.oh-my-zsh"
# Disable OMZ's theme in favour or Pure Prompt
ZSH_THEME=""

# Perf
DISABLE_UNTRACKED_FILES_DIRTY="true"
plugins=(git npm zsh-syntax-highlighting)

source $ZSH/oh-my-zsh.sh
# --- /Oh My Zsh ---

export LANG=en_US.UTF-8
export EDITOR='hx'
export VISUAL="$EDITOR"

# NVM
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  

# PURE prompt https://github.com/sindresorhus/pure
# (Installed via git in ~/.zsh/pure/)
fpath+=($HOME/.zsh/pure)
autoload -U promptinit; promptinit
prompt pure

# all aliases
alias zshconfig="hx ~/.zshrc"
alias kittyconfig="hx ~/.config/kitty/kitty.conf"
alias helixconfig="hx ~/.config/helix/config.toml"
alias ohmyzsh="hx ~/.oh-my-zsh"
alias vim="hx"
alias gs="git status"

