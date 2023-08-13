#!/bin/sh

export DOTFILES="$HOME/git/dotfiles"

# ---- Oh My Zsh ----

export ZSH="$DOTFILES/ohmyzsh"
# Disable OMZ's theme in favour or Pure Prompt
ZSH_THEME=""

# Perf
DISABLE_UNTRACKED_FILES_DIRTY="true"
plugins=(git npm zsh-syntax-highlighting)

source $ZSH/oh-my-zsh.sh

# PURE prompt installed as submodule 
# https://github.com/sindresorhus/pure
fpath+=($DOTFILES/pure)
autoload -U promptinit; promptinit
prompt pure

# --- /Oh My Zsh ---

export LANG=en_US.UTF-8
export EDITOR='hx'
export VISUAL="$EDITOR"

# NVM
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  

# all aliases
alias zshconfig="hx ~/.zshrc"
alias kittyconfig="hx ~/.config/kitty/kitty.conf"
alias helixconfig="hx ~/.config/helix/config.toml"
alias ohmyzsh="hx ~/.oh-my-zsh"
alias vim="hx"
alias gs="git status"

