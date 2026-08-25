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

path+=("$DOTFILES/diff-so-fancy")
path+=("$HOME/.cargo/bin")
export PATH

# NVM
export NVM_DIR="$DOTFILES/nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

# all aliases
alias zshconfig="hx ~/.zshrc"
alias kittyconfig="hx ~/.config/kitty/kitty.conf"
alias helixconfig="hx ~/.config/helix/config.toml"
alias ohmyzsh="hx ~/.oh-my-zsh"
alias vim="hx"
alias gs="git status"
alias icat="kitty +kitten icat"
alias copy-need-raw="node $DOTFILES/scripts/copy-need-raw/index.mjs"
alias ssh-eye="ssh ubuntu@57.129.77.80 -p 55641"


# see https://www.crackedthecode.co/how-to-use-your-dslr-as-a-webcam-in-linux/#debianubuntu for more commands. This starts the acquisitions of the stream
alias mirrorless-webcam="gphoto2 --stdout --capture-movie | ffmpeg -i - -vcodec rawvideo -pix_fmt yuv420p -threads 0 -f v4l2 /dev/video0"


# >>> spawn >>>
export PATH="/home/thibaut/.local/bin:$PATH"
# <<< spawn <<<

# >>> spawn >>>
export PATH="/home/thibaut/.bun/bin:$PATH"
# <<< spawn <<<
[ -f ~/.spawnrc ] && source ~/.spawnrc
