#!/bin/sh

echo "Setting up Fedora..."

if [ ! -d "$HOME/git" ]; then
	mkdir $HOME/git
fi


# setup dotfiles
rm -rf $HOME/.zshrc
ln -s -r .zshrc $HOME/.zshrc

rm -rf $HOME/.config/helix/
ln -s -r helix $HOME/.config/helix
